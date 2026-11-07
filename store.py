from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from .models import OutgoingAction, OutgoingStatus
except ImportError:  # pragma: no cover - direct Runner fallback
    from models import OutgoingAction, OutgoingStatus


class OperatorStore:
    """小黑盒插件自己的 SQLite 状态，不触碰 MaiBot 主库。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cursors (
                profile_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                cursor TEXT NOT NULL DEFAULT '',
                baseline_ready INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (profile_id, kind)
            );
            CREATE TABLE IF NOT EXISTS incoming_events (
                event_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                claimed_at REAL,
                state TEXT NOT NULL DEFAULT 'done',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS outgoing_actions (
                action_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outgoing_status ON outgoing_actions(status);
            CREATE INDEX IF NOT EXISTS idx_outgoing_created ON outgoing_actions(created_at);
            CREATE TABLE IF NOT EXISTS seen_posts (
                post_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                excerpt TEXT NOT NULL DEFAULT '',
                community TEXT NOT NULL DEFAULT '',
                seen_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_seen_posts_seen ON seen_posts(seen_at DESC);
            CREATE TABLE IF NOT EXISTS digest_reads (
                post_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                excerpt TEXT NOT NULL DEFAULT '',
                community TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                batch TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_digest_pending ON digest_reads(batch, created_at);
            CREATE TABLE IF NOT EXISTS digest_runs (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                state TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                draft_json TEXT NOT NULL DEFAULT '',
                link_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(incoming_events)")}
        migrated_claims = "state" not in columns
        if migrated_claims:
            self._conn.execute(
                "ALTER TABLE incoming_events ADD COLUMN state TEXT NOT NULL DEFAULT 'done'"
            )
            self._conn.execute(
                "ALTER TABLE incoming_events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "ALTER TABLE incoming_events ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "ALTER TABLE incoming_events ADD COLUMN last_error TEXT NOT NULL DEFAULT ''"
            )
            # Older releases recorded an event as claimed before attempting its
            # reply. Recover recent orphaned claims once, without reopening an
            # action that was already sent, dry-run, suppressed, or uncertain.
            self._conn.execute(
                """
                UPDATE incoming_events
                SET state='pending', next_attempt_at=0, last_error='recovered_unfinished'
                WHERE created_at >= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM outgoing_actions
                    WHERE outgoing_actions.action_key = 'reply:' || incoming_events.event_key
                  )
                """,
                (time.time() - 48 * 60 * 60,),
            )
        self._conn.commit()

    def get_cursor(self, profile_id: str, kind: str) -> tuple[str, bool]:
        row = self._conn.execute(
            "SELECT cursor, baseline_ready FROM cursors WHERE profile_id=? AND kind=?",
            (profile_id, kind),
        ).fetchone()
        return (str(row["cursor"]), bool(row["baseline_ready"])) if row else ("", False)

    def set_cursor(self, profile_id: str, kind: str, cursor: str, *, baseline_ready: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO cursors(profile_id, kind, cursor, baseline_ready, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, kind) DO UPDATE SET
              cursor=excluded.cursor,
              baseline_ready=excluded.baseline_ready,
              updated_at=excluded.updated_at
            """,
            (profile_id, kind, str(cursor), int(baseline_ready), time.time()),
        )
        self._conn.commit()

    def remember_incoming_baseline(
        self, event_key: str, kind: str, payload: dict[str, Any]
    ) -> None:
        """Record a notification already present when polling is initialized."""
        now = time.time()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO incoming_events(
                event_key, kind, payload_json, created_at, claimed_at, state
            ) VALUES (?, ?, ?, ?, ?, 'done')
            """,
            (event_key, kind, json.dumps(payload, ensure_ascii=False), now, now),
        )
        self._conn.commit()

    def has_incoming(self, event_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM incoming_events WHERE event_key=? LIMIT 1", (event_key,)
        ).fetchone()
        return row is not None

    def claim_incoming(self, event_key: str, kind: str, payload: dict[str, Any]) -> bool:
        now = time.time()
        try:
            self._conn.execute(
                "INSERT INTO incoming_events(event_key, kind, payload_json, created_at, claimed_at, state) VALUES (?, ?, ?, ?, ?, 'pending')",
                (event_key, kind, json.dumps(payload, ensure_ascii=False), now, now),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_incoming_done(self, event_key: str, *, state: str = "done") -> None:
        self._conn.execute(
            "UPDATE incoming_events SET state=?, last_error='', next_attempt_at=0 WHERE event_key=?",
            (state, event_key),
        )
        self._conn.commit()

    def defer_incoming(
        self,
        event_key: str,
        error: str,
        *,
        delay_seconds: float = 60,
        increment_attempt: bool = True,
        max_attempts: int = 8,
    ) -> None:
        bump = 1 if increment_attempt else 0
        self._conn.execute(
            """
            UPDATE incoming_events
            SET attempts=attempts+?,
                state=CASE WHEN attempts+? >= ? THEN 'failed' ELSE 'pending' END,
                next_attempt_at=CASE WHEN attempts+? >= ? THEN 0 ELSE ? END,
                last_error=?
            WHERE event_key=?
            """,
            (
                bump,
                bump,
                max(1, max_attempts),
                bump,
                max(1, max_attempts),
                time.time() + max(1, delay_seconds),
                str(error)[:300],
                event_key,
            ),
        )
        self._conn.commit()

    def list_pending_incoming(
        self, *, kind: str | None = None, limit: int = 50, now: float | None = None
    ) -> list[dict[str, Any]]:
        current = time.time() if now is None else now
        bounded_limit = max(1, min(limit, 200))
        if kind is None:
            rows = self._conn.execute(
                "SELECT * FROM incoming_events WHERE state='pending' AND next_attempt_at<=? ORDER BY created_at ASC LIMIT ?",
                (current, bounded_limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM incoming_events WHERE state='pending' AND next_attempt_at<=? AND kind=? ORDER BY created_at ASC LIMIT ?",
                (current, kind, bounded_limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(str(item.pop("payload_json", "{}")))
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result

    def get_incoming(self, event_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM incoming_events WHERE event_key=? LIMIT 1", (event_key,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["payload"] = json.loads(str(item.pop("payload_json", "{}")))
        except json.JSONDecodeError:
            item["payload"] = {}
        return item

    def begin_outgoing(self, action: OutgoingAction) -> bool:
        now = time.time()
        try:
            self._conn.execute(
                """
                INSERT INTO outgoing_actions(
                  action_key, kind, target_id, content, status, external_id,
                  request_id, error, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.action_key,
                    action.kind,
                    action.target_id,
                    action.content,
                    action.status.value,
                    action.external_id,
                    action.request_id,
                    action.error,
                    json.dumps(action.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            existing = self._conn.execute(
                "SELECT status, external_id FROM outgoing_actions WHERE action_key=?",
                (action.action_key,),
            ).fetchone()
            if existing and existing[0] == OutgoingStatus.FAILED.value and not str(existing[1] or ""):
                self._conn.execute(
                    "UPDATE outgoing_actions SET status=?, error='', updated_at=? WHERE action_key=?",
                    (action.status.value, now, action.action_key),
                )
                self._conn.commit()
                return True
            return False

    def update_outgoing(
        self,
        action_key: str,
        status: OutgoingStatus,
        *,
        external_id: str = "",
        request_id: str = "",
        error: str = "",
    ) -> None:
        self._conn.execute(
            """
            UPDATE outgoing_actions
            SET status=?, external_id=COALESCE(NULLIF(?, ''), external_id),
                request_id=COALESCE(NULLIF(?, ''), request_id), error=?, updated_at=?
            WHERE action_key=?
            """,
            (status.value, external_id, request_id, error[:500], time.time(), action_key),
        )
        self._conn.commit()

    def merge_outgoing_metadata(self, action_key: str, updates: dict[str, Any]) -> None:
        """Merge small audit markers without replacing the original request."""

        if not updates:
            return
        row = self._conn.execute(
            "SELECT metadata_json FROM outgoing_actions WHERE action_key=? LIMIT 1",
            (action_key,),
        ).fetchone()
        if row is None:
            return
        try:
            metadata = json.loads(str(row[0] or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update(updates)
        self._conn.execute(
            "UPDATE outgoing_actions SET metadata_json=?, updated_at=? WHERE action_key=?",
            (json.dumps(metadata, ensure_ascii=False), time.time(), action_key),
        )
        self._conn.commit()

    def count_since(self, kind: str, since: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM outgoing_actions WHERE kind=? AND created_at>=? AND status NOT IN (?, ?, ?)",
            (
                kind,
                since,
                OutgoingStatus.SUPPRESSED.value,
                OutgoingStatus.FAILED.value,
                OutgoingStatus.DRY_RUN.value,
            ),
        ).fetchone()
        return int(row["count"] if row else 0)

    def last_outgoing_at(
        self,
        kind: str | None = None,
        *,
        target_id: str | None = None,
        exclude_action_key: str = "",
        exclude_kinds: tuple[str, ...] = (),
    ) -> float | None:
        """Return the most recent non-terminal outgoing timestamp."""
        params: list[object] = []
        clauses = [
            "status NOT IN (?, ?, ?)",
        ]
        params.extend(
            [
                OutgoingStatus.SUPPRESSED.value,
                OutgoingStatus.FAILED.value,
                OutgoingStatus.DRY_RUN.value,
            ]
        )
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if target_id is not None:
            clauses.append("target_id=?")
            params.append(str(target_id))
        if exclude_action_key:
            clauses.append("action_key<>?")
            params.append(exclude_action_key)
        if exclude_kinds:
            clauses.append(f"kind NOT IN ({', '.join('?' for _ in exclude_kinds)})")
            params.extend(exclude_kinds)
        query = (
            "SELECT updated_at FROM outgoing_actions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC LIMIT 1"
        )
        row = self._conn.execute(query, tuple(params)).fetchone()
        return float(row["updated_at"]) if row else None

    def record_seen_post(
        self,
        post_id: str,
        *,
        title: str,
        excerpt: str = "",
        community: str = "",
        seen_at: float | None = None,
    ) -> None:
        """Remember one post the account actually opened today."""

        safe_id = str(post_id or "").strip()
        safe_title = str(title or "").strip()
        if not safe_id or not safe_title:
            return
        now = time.time() if seen_at is None else float(seen_at)
        self._conn.execute(
            """
            INSERT INTO seen_posts(post_id, title, excerpt, community, seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
              title=excluded.title,
              excerpt=excluded.excerpt,
              community=excluded.community,
              seen_at=excluded.seen_at
            """,
            (
                safe_id,
                safe_title[:120],
                str(excerpt or "")[:200],
                str(community or "")[:60],
                now,
            ),
        )
        # Keep the table small: only the last week can feed a "today" summary.
        self._conn.execute("DELETE FROM seen_posts WHERE seen_at < ?", (now - 7 * 24 * 60 * 60,))
        self._conn.commit()

    def list_seen_posts_since(self, since: float, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT post_id, title, excerpt, community, seen_at
            FROM seen_posts
            WHERE seen_at >= ?
            ORDER BY seen_at DESC
            LIMIT ?
            """,
            (float(since), max(1, min(int(limit), 50))),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_digest_material(
        self,
        post_id: str,
        *,
        title: str,
        excerpt: str = "",
        community: str = "",
    ) -> bool:
        """Accumulate one browsed post for the summary post (dedup by post)."""

        safe_id = str(post_id or "").strip()
        safe_title = str(title or "").strip()
        if not safe_id or not safe_title:
            return False
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO digest_reads(post_id, title, excerpt, community, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (safe_id, safe_title[:120], str(excerpt or "")[:300], str(community or "")[:60], time.time()),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count_digest_pending(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM digest_reads WHERE batch=''"
        ).fetchone()
        return int(row["count"] if row else 0)

    def claim_digest(
        self,
        *,
        trigger: str,
        every_posts: int,
        interval_hours: int,
        topic: str = "",
        force: bool = False,
    ) -> str:
        """Claim one batch of materials when the configured trigger is due."""

        now = time.time()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            # A pending implicit transaction on the shared connection.
            self._conn.commit()
            self._conn.execute("BEGIN IMMEDIATE")
        try:
            latest = self._conn.execute(
                "SELECT state, created_at FROM digest_runs ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            if latest is not None and str(latest["state"]) not in {"sent", "discarded"}:
                self._conn.rollback()
                return ""
            row = self._conn.execute(
                "SELECT COUNT(*) AS count, MIN(created_at) AS first_at FROM digest_reads WHERE batch=''"
            ).fetchone()
            count = int(row["count"] if row else 0)
            first_at = float(row["first_at"] or 0) if row else 0.0
            if count <= 0:
                self._conn.rollback()
                return ""
            limit = max(1, min(int(every_posts), 100))
            count_due = count >= limit
            base = max(first_at or now, float(latest["created_at"]) if latest else (first_at or now))
            time_due = now >= base + max(1, int(interval_hours)) * 3600
            normalized = str(trigger or "count").lower()
            if normalized == "interval":
                due = time_due
            elif normalized == "either":
                due = count_due or time_due
            else:
                due = count_due
            if not force and not due:
                self._conn.rollback()
                return ""
            batch = uuid.uuid4().hex
            self._conn.execute(
                "INSERT INTO digest_runs(id, created_at, state, topic) VALUES (?, ?, 'generating', ?)",
                (batch, now, str(topic or "")[:32]),
            )
            take = limit if (count_due or normalized == "count") else 100
            self._conn.execute(
                """
                UPDATE digest_reads SET batch=? WHERE post_id IN (
                    SELECT post_id FROM digest_reads WHERE batch='' ORDER BY created_at, rowid LIMIT ?
                )
                """,
                (batch, take),
            )
            self._conn.commit()
            return batch
        except Exception:
            self._conn.rollback()
            raise

    def digest_materials(self, batch: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT post_id, title, excerpt, community, created_at
            FROM digest_reads WHERE batch=? ORDER BY created_at, rowid
            """,
            (str(batch),),
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_digest_run(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, created_at, state, topic, draft_json, link_id, error FROM digest_runs ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def update_digest_run(
        self,
        run_id: str,
        state: str,
        *,
        draft_json: str | None = None,
        link_id: str | None = None,
        error: str = "",
    ) -> None:
        self._conn.execute(
            """
            UPDATE digest_runs
            SET state=?, draft_json=COALESCE(?, draft_json),
                link_id=COALESCE(?, link_id), error=?
            WHERE id=?
            """,
            (str(state), draft_json, link_id, str(error)[:500], str(run_id)),
        )
        self._conn.commit()

    def digest_comment_materials(self, post_id: str, *, limit: int = 3) -> list[str]:
        """The account's own verified comments under one post."""

        rows = self._conn.execute(
            """
            SELECT content, metadata_json FROM outgoing_actions
            WHERE kind IN ('notification_reply', 'proactive_comment') AND status='verified'
            ORDER BY updated_at DESC LIMIT 200
            """
        ).fetchall()
        found: list[str] = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            if str(metadata.get("post_id") or "") != str(post_id):
                continue
            text = str(row["content"] or "").strip()
            if text:
                found.append(text)
            if len(found) >= max(1, int(limit)):
                break
        return list(reversed(found))

    def has_outgoing_key(self, action_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM outgoing_actions WHERE action_key=? LIMIT 1",
            (action_key,),
        ).fetchone()
        return row is not None

    def get_outgoing(self, action_key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM outgoing_actions WHERE action_key=? LIMIT 1", (action_key,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        try:
            item["metadata"] = json.loads(str(item.pop("metadata_json", "{}")))
        except json.JSONDecodeError:
            item["metadata"] = {}
        return item

    def list_outgoing(self, *, status: OutgoingStatus | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM outgoing_actions ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM outgoing_actions WHERE status=? ORDER BY updated_at ASC LIMIT ?",
                (status.value, max(1, min(limit, 200))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(str(item.pop("metadata_json", "{}")))
            except json.JSONDecodeError:
                item["metadata"] = {}
            result.append(item)
        return result

    def close(self) -> None:
        self._conn.close()


"""运维命令：立即检查、失败重试、取消队列、发帖预览。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from models import OutgoingAction, OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class


class _Generator:
    def __init__(self, reply="[cube_狗头] 这帖挺有意思"):
        self.text = reply
        self.calls = []

    async def reply(self, **kwargs):
        self.calls.append(kwargs)
        return self.text


class _Client:
    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        return SimpleNamespace(title="测试帖", body="正文内容")


class _Runtime:
    credentials = object()

    async def client(self):
        return _Client()


class _Logger:
    def __init__(self):
        self.lines = []

    def _add(self, message, *args, **kwargs):
        self.lines.append(message % args if args else str(message))

    info = warning = error = debug = _add


class AdminCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(self, directory: Path):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.basic.mode = "auto"
        plugin._store = OperatorStore(directory / "state.db")
        plugin._runtime = _Runtime()
        plugin._generator = _Generator()
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def _seed(
        self,
        plugin,
        action_key: str,
        *,
        kind: str = "notification_reply",
        target: str = "100",
        status: OutgoingStatus = OutgoingStatus.FAILED,
        error: str = "upstream_rejected",
        metadata=None,
    ) -> None:
        action = OutgoingAction(
            action_key, kind, target, "内容", metadata=metadata or {"post_id": target}
        )
        plugin._store.begin_outgoing(action)
        plugin._store.update_outgoing(action_key, status, error=error)

    def test_failed_comment_can_be_requeued(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin, "reply:e1", error="upstream_rejected:500")
            self._seed(plugin, "reply:e2", error="cancelled_by_user")
            self._seed(plugin, "dm_reply:m1", kind="dm_reply", error="dm_restricted")

            self.assertEqual(plugin._requeue_failed_comments(), 1)
            row = plugin._store.get_outgoing("reply:e1")
            self.assertEqual(row["status"], OutgoingStatus.SUPPRESSED.value)
            self.assertEqual(row["error"], "manual_retry")
            self.assertEqual(
                plugin._store.get_outgoing("reply:e2")["status"],
                OutgoingStatus.FAILED.value,
            )
            plugin._store.close()

    def test_cancel_queue_can_filter_by_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            plugin._store.claim_incoming("e1", "reply", {"event_key": "e1", "post_id": "100"})
            self._seed(
                plugin,
                "reply:e1",
                status=OutgoingStatus.SUPPRESSED,
                error="cooldown",
                metadata={"post_id": "100", "event_key": "e1"},
            )
            self._seed(
                plugin,
                "feed_comment:200",
                kind="proactive_comment",
                target="200",
                status=OutgoingStatus.PLANNED,
                metadata={"post_id": "200"},
            )

            self.assertEqual(plugin._cancel_pending_comments("100"), 1)
            self.assertEqual(
                plugin._store.get_outgoing("reply:e1")["status"],
                OutgoingStatus.FAILED.value,
            )
            self.assertEqual(
                plugin._store.get_outgoing("reply:e1")["error"],
                "cancelled_by_user",
            )
            self.assertEqual(plugin._store.get_incoming("e1")["state"], "ignored")
            self.assertEqual(
                plugin._store.get_outgoing("feed_comment:200")["status"],
                OutgoingStatus.PLANNED.value,
            )
            plugin._store.close()

    def test_cancel_queue_without_filter_clears_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin, "reply:e1", status=OutgoingStatus.PLANNED)
            self._seed(
                plugin,
                "feed_comment:200",
                kind="proactive_comment",
                target="200",
                status=OutgoingStatus.PLANNED,
            )
            self.assertEqual(plugin._cancel_pending_comments(), 2)
            plugin._store.close()

    def test_preview_reply_normalizes_emoji_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            result = asyncio.run(plugin._preview_reply("100"))
            self.assertTrue(result["success"])
            self.assertEqual(result["draft"], "[cube_doge] 这帖挺有意思")
            self.assertTrue(result["allowed"])
            self.assertEqual(plugin._store.list_outgoing(), [])
            plugin._store.close()

    def test_preview_rejects_empty_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            plugin._generator = _Generator(reply="SKIP")
            result = asyncio.run(plugin._preview_reply("100"))
            self.assertFalse(result["success"])
            self.assertIn("SKIP", str(result["message"]))
            plugin._store.close()

    def test_command_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin, "reply:e1")

            ok, text, _ = asyncio.run(plugin.handle_help_command())
            self.assertTrue(ok)
            self.assertIn("/xhh_check", text)

            ok, text, _ = asyncio.run(plugin.handle_retry_command())
            self.assertTrue(ok)
            self.assertIn("1 条", text)

            ok, text, _ = asyncio.run(
                plugin.handle_clear_queue_command(matched_groups={"post_id": "100"})
            )
            self.assertTrue(ok)
            self.assertIn("1 条", text)

            ok, text, _ = asyncio.run(
                plugin.handle_test_command(matched_groups={"post_id": "100"})
            )
            self.assertTrue(ok)
            self.assertIn("预览回复", text)
            plugin._store.close()

    def test_whoami_reports_scoped_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            ok, text, _ = asyncio.run(
                plugin.handle_whoami_command(
                    platform="QQ", user_id="1249922508", session_id="session-1"
                )
            )
            self.assertTrue(ok)
            self.assertIn("platform:id=qq:1249922508", text)
            self.assertIn("session_id=session-1", text)
            plugin._store.close()

    def test_check_command_runs_one_poll(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            calls = []

            async def fake_poll():
                calls.append(1)

            plugin._poll_once = fake_poll
            ok, text, _ = asyncio.run(plugin.handle_check_command())
            self.assertTrue(ok)
            self.assertEqual(calls, [1])
            self.assertIn("已执行一轮检查", text)
            plugin._store.close()


if __name__ == "__main__":
    unittest.main()

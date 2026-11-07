"""私信回复流程：基线、白/黑名单、去重、dry_run、限制暂停、配图。"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from dm_store import DirectMessageStore
from models import OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class
from vendor_xiaoheihe.models import (
    DirectConversation,
    DirectConversationPage,
    DirectMessage,
    DirectMessagePage,
    DirectMessageSendResult,
)


class _Logger:
    def __init__(self):
        self.lines = []

    def _add(self, message, *args, **kwargs):
        self.lines.append(message % args if args else str(message))

    info = warning = error = debug = _add


class _Generator:
    def __init__(self, reply="在的，刚看到，咋啦？"):
        self.reply = reply
        self.calls = []

    async def direct_message_reply(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


class _Restricted(Exception):
    category = "dm_restricted"


class _ImageGenerator:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.prompts = []

    async def generate(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("image provider down")
        return b"\x89PNG\r\n\x1a\n" + b"x" * 64


class _FakeClient:
    def __init__(self, *, conversations=None, messages=None, error=None, upload_error=None, image_url="https://cdn.example.com/dm.png"):
        self.conversations = list(conversations or [])
        self.messages = {peer: list(items) for peer, items in (messages or {}).items()}
        self.error = error
        self.upload_error = upload_error
        self.image_url = image_url
        self.sent = []
        self.uploads = []

    async def fetch_direct_conversations(self, *, limit=20):
        return DirectConversationPage(items=list(self.conversations), source="user_messages")

    async def fetch_direct_messages(self, *, peer_uid, limit=30, offset=0):
        return DirectMessagePage(
            items=list(self.messages.get(peer_uid, [])), source="direct_message_inbox"
        )

    async def upload_image_bytes(self, image_bytes, *, filename="x.png", mime_type="image/png"):
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append((filename, len(image_bytes)))
        return {"url": self.image_url, "width": 1024, "height": 1024}

    async def send_direct_message(self, *, peer_uid, text, image_url=""):
        if self.error is not None:
            raise self.error
        self.sent.append({"peer": peer_uid, "text": text, "image": image_url})
        return DirectMessageSendResult(
            heychat_ack_id=f"ack-{len(self.sent)}",
            message_id=f"srv-{len(self.sent)}",
            message_seq=str(len(self.sent)),
        )


def _conversation(peer: str, marker: str) -> DirectConversation:
    return DirectConversation(profile_id="default", peer_uid=peer, peer_name="盒友", marker=marker)


def _message(message_id: str, text: str, *, peer: str = "peer-9", incoming: bool = True) -> DirectMessage:
    return DirectMessage(
        profile_id="default",
        message_id=message_id,
        conversation_id=f"dm!{peer}",
        sender_uid=peer if incoming else "me-1",
        sender_name="盒友",
        text=text,
        created_at=time.time(),
        incoming=incoming,
        peer_uid=peer,
    )


class DirectMessageReplyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(
        self,
        directory: Path,
        *,
        mode="auto",
        dm_mode="auto",
        scope="allowlist",
        allowlist=("peer-9",),
        blocklist=(),
        quota=5,
        images=False,
    ):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.basic.mode = mode
        plugin.config.dm.dm_enabled = True
        plugin.config.dm.dm_mode = dm_mode
        plugin.config.dm.dm_daily_quota = quota
        plugin.config.dm.dm_cooldown_seconds = 0
        plugin.config.dm.dm_reply_scope = scope
        plugin.config.dm.dm_user_allowlist = list(allowlist)
        plugin.config.dm.dm_user_blocklist = list(blocklist)
        plugin.config.dm.dm_generate_images = images
        plugin._store = OperatorStore(directory / "state.db")
        plugin._dm_store = DirectMessageStore(directory / "state.db")
        plugin._generator = _Generator()
        plugin._persona = None
        plugin._drawing = None
        plugin._image_gen = None
        plugin._runtime = SimpleNamespace(credentials=None)
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def _run(self, plugin, client) -> None:
        asyncio.run(plugin._poll_direct_messages(client))

    def _prime(self, plugin, *, messages, marker="m1", peer="peer-9"):
        self._run(
            plugin,
            _FakeClient(
                conversations=[_conversation(peer, marker)],
                messages={peer: messages},
            ),
        )

    def test_baseline_does_not_reply_to_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m1")],
                messages={"peer-9": [_message("m1", "之前的私信")]},
            )
            self._run(plugin, client)
            self.assertEqual(client.sent, [])
            self.assertEqual(plugin._store.list_outgoing(), [])
            self.assertTrue(plugin._dm_store.get_cursor("default")[1])
            plugin._store.close()
            plugin._dm_store.close()

    def test_new_message_is_answered_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m2")],
                messages={"peer-9": history + [_message("m2", "在吗？")]},
            )
            self._run(plugin, client)

            self.assertEqual(
                client.sent,
                [{"peer": "peer-9", "text": "在的，刚看到，咋啦？", "image": ""}],
            )
            rows = plugin._store.list_outgoing()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["action_key"], "dm_reply:m2")
            self.assertEqual(rows[0]["status"], OutgoingStatus.VERIFIED.value)
            self.assertEqual(rows[0]["external_id"], "srv-1")
            plugin._store.close()
            plugin._dm_store.close()

    def test_repeated_poll_never_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            payload = history + [_message("m2", "在吗？")]
            for _ in range(3):
                self._run(
                    plugin,
                    _FakeClient(
                        conversations=[_conversation("peer-9", "m2")],
                        messages={"peer-9": payload},
                    ),
                )
            self.assertEqual(len(plugin._store.list_outgoing()), 1)
            plugin._store.close()
            plugin._dm_store.close()

    def test_allowlist_blocks_strangers(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), allowlist=("someone-else",))
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m1")],
                messages={"peer-9": [_message("m1", "陌生人私信")]},
            )
            self._run(plugin, client)
            self.assertEqual(client.sent, [])
            plugin._store.close()
            plugin._dm_store.close()

    def test_blocklist_scope_replies_to_everyone_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), scope="blocklist", allowlist=(), blocklist=("peer-bad",))
            history = [
                _message("a1", "你好", peer="peer-9"),
                _message("b1", "买号吗", peer="peer-bad"),
            ]
            self._run(
                plugin,
                _FakeClient(
                    conversations=[
                        _conversation("peer-9", "a1"),
                        _conversation("peer-bad", "b1"),
                    ],
                    messages={"peer-9": history[:1], "peer-bad": history[1:]},
                ),
            )
            client = _FakeClient(
                conversations=[
                    _conversation("peer-9", "a2"),
                    _conversation("peer-bad", "b2"),
                ],
                messages={
                    "peer-9": [_message("a1", "你好", peer="peer-9"), _message("a2", "在吗", peer="peer-9")],
                    "peer-bad": [_message("b1", "买号吗", peer="peer-bad"), _message("b2", "打扰了", peer="peer-bad")],
                },
            )
            self._run(plugin, client)
            self.assertEqual([item["peer"] for item in client.sent], ["peer-9"])
            plugin._store.close()
            plugin._dm_store.close()

    def test_blocklist_wins_over_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), allowlist=("peer-9",), blocklist=("peer-9",))
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m2")],
                messages={"peer-9": history + [_message("m2", "在吗？")]},
            )
            self._run(plugin, client)
            self.assertEqual(client.sent, [])
            plugin._store.close()
            plugin._dm_store.close()

    def test_dry_run_records_draft_without_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), dm_mode="dry_run")
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m2")],
                messages={"peer-9": history + [_message("m2", "在吗？")]},
            )
            self._run(plugin, client)
            self.assertEqual(client.sent, [])
            rows = plugin._store.list_outgoing()
            self.assertEqual(rows[0]["status"], OutgoingStatus.DRY_RUN.value)
            plugin._store.close()
            plugin._dm_store.close()

    def test_restriction_pauses_following_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m3")],
                messages={
                    "peer-9": history
                    + [_message("m2", "在吗？"), _message("m3", "怎么不理我")]
                },
                error=_Restricted("私信功能已被限制"),
            )
            self._run(plugin, client)
            rows = plugin._store.list_outgoing()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["error"], "dm_restricted")
            self.assertGreater(plugin._dm_blocked_until, 0)
            plugin._store.close()
            plugin._dm_store.close()

    def test_quota_zero_never_generates(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), quota=0)
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m2")],
                messages={"peer-9": history + [_message("m2", "在吗？")]},
            )
            self._run(plugin, client)
            self.assertEqual(plugin._generator.calls, [])
            self.assertEqual(client.sent, [])
            plugin._store.close()
            plugin._dm_store.close()

    def test_reply_can_attach_generated_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), images=True)
            plugin._image_gen = _ImageGenerator()
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m2")],
                messages={"peer-9": history + [_message("m2", "发张照片看看")]},
            )
            self._run(plugin, client)
            self.assertEqual(len(client.sent), 1)
            self.assertEqual(client.sent[0]["image"], "https://cdn.example.com/dm.png")
            self.assertEqual(len(client.uploads), 1)
            row = plugin._store.list_outgoing()[0]
            self.assertTrue(row["metadata"]["image_attached"])
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            plugin._store.close()
            plugin._dm_store.close()

    def test_image_failure_falls_back_to_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), images=True)
            plugin._image_gen = _ImageGenerator(fail=True)
            history = [_message("m1", "之前的私信")]
            self._prime(plugin, messages=history)
            client = _FakeClient(
                conversations=[_conversation("peer-9", "m2")],
                messages={"peer-9": history + [_message("m2", "发张照片看看")]},
            )
            self._run(plugin, client)
            self.assertEqual(client.sent[0]["image"], "")
            row = plugin._store.list_outgoing()[0]
            self.assertFalse(row["metadata"]["image_attached"])
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            plugin._store.close()
            plugin._dm_store.close()


if __name__ == "__main__":
    unittest.main()

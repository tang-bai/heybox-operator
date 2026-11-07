"""评论读回核验：楼中楼、空白差异、近期评论兜底、按 ID 命中。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from models import OutgoingAction, OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class
from vendor_xiaoheihe.models import RoutingTarget


class _Logger:
    def _add(self, *args, **kwargs):
        pass

    info = warning = error = debug = _add


class _Client:
    def __init__(self, comments=None, recent=None, context_error=None):
        self.comments = list(comments or [])
        self.recent = list(recent or [])
        self.context_error = context_error

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        if self.context_error is not None:
            raise self.context_error
        return SimpleNamespace(comments=list(self.comments))

    async def recent_comments(self, route, *, limit=20):
        return list(self.recent)


ROUTE = RoutingTarget(profile_id="default", post_id="100", root_comment_id="900", parent_comment_id="901")


class CommentReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(self):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def _verify(self, client, content, external=""):
        return asyncio.run(
            self._plugin()._verify_comment(client, ROUTE, content, external_comment_id=external)
        )

    def test_nested_floor_reply_is_found(self):
        client = _Client(
            comments=[
                {
                    "commentid": "900",
                    "text": "根评论",
                    "comments": [{"commentid": "901", "text": "我们的回复在这里"}],
                }
            ]
        )
        self.assertTrue(self._verify(client, "我们的回复在这里"))

    def test_whitespace_differences_do_not_break_readback(self):
        client = _Client(comments=[{"commentid": "1", "text": "你好　世界 再见"}])
        self.assertTrue(self._verify(client, "你好 世界再见"))

    def test_recent_comments_fallback_with_at_prefix(self):
        client = _Client(
            comments=[],
            recent=[{"commentid": "77", "text": "回复 @盒友：今天也在打游戏吗"}],
        )
        self.assertTrue(self._verify(client, "今天也在打游戏吗"))

    def test_external_comment_id_is_enough(self):
        client = _Client(comments=[], recent=[{"commentid": "555", "text": "平台改写过的文本"}])
        self.assertTrue(self._verify(client, "原始回复", external="555"))

    def test_missing_comment_is_not_verified(self):
        client = _Client(comments=[{"commentid": "1", "text": "别的评论"}])
        self.assertFalse(self._verify(client, "我们的回复"))

    def test_thread_error_falls_back_to_recent(self):
        client = _Client(
            recent=[{"commentid": "9", "text": "今天的回复内容"}],
            context_error=RuntimeError("thread unavailable"),
        )
        self.assertTrue(self._verify(client, "今天的回复内容"))

    def test_reconcile_promotes_unknown_to_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin()
            plugin._store = OperatorStore(Path(tmp) / "state.db")
            action = OutgoingAction(
                "reply:e1",
                "notification_reply",
                "100",
                "今天也在打游戏吗",
                metadata={"post_id": "100", "root_comment_id": "900"},
            )
            plugin._store.begin_outgoing(action)
            plugin._store.update_outgoing(
                "reply:e1", OutgoingStatus.SEND_UNKNOWN, error="readback_not_found"
            )
            client = _Client(
                recent=[{"commentid": "9", "text": "今天也在打游戏吗"}],
            )
            asyncio.run(plugin._reconcile_unknowns(client))
            row = plugin._store.get_outgoing("reply:e1")
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            plugin._store.close()


if __name__ == "__main__":
    unittest.main()

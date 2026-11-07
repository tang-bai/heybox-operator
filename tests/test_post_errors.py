"""主动发帖的错误分类：驳回、读回失败、缺 link_id，以及事后核验。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from models import OutgoingAction, OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class
from vendor_xiaoheihe.api_client import XiaoheiheApiError


class _Logger:
    def _add(self, *args, **kwargs):
        pass

    info = warning = error = debug = _add


class _Generator:
    async def post(self, **kwargs):
        return "测试标题", "测试正文内容"


class _Persona:
    async def life_context(self):
        return "当前活动：吃饭"


class _Client:
    def __init__(
        self,
        *,
        create_result=None,
        create_error=None,
        context_error=None,
        context=None,
    ):
        self.create_result = create_result
        self.create_error = create_error
        self.context_error = context_error
        self.context = context

    async def resolve_topic_names(self, names):
        return {name: "1001" for name in names}

    async def create_post(self, **kwargs):
        if self.create_error is not None:
            raise self.create_error
        return self.create_result if self.create_result is not None else {"link_id": "L1"}

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        if self.context_error is not None:
            raise self.context_error
        return self.context


class ProactivePostErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(self, directory: Path):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.basic.mode = "auto"
        plugin.config.automation.post_active_start_time = "00:00"
        plugin.config.automation.post_active_end_time = "00:00"
        plugin.config.automation.post_target_primary = "PC游戏"
        plugin.config.content.generate_post_images = False
        plugin.config.content.post_summarize_feed = False
        plugin._store = OperatorStore(directory / "state.db")
        plugin._generator = _Generator()
        plugin._persona = _Persona()
        plugin._drawing = None
        plugin._image_gen = None
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def test_rejected_post_is_failed_with_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            client = _Client(
                create_error=XiaoheiheApiError(
                    "分区不存在", category="upstream_rejected"
                )
            )
            asyncio.run(plugin._create_proactive_post(client))
            row = plugin._store.list_outgoing()[0]
            self.assertEqual(row["status"], OutgoingStatus.FAILED.value)
            self.assertTrue(row["error"].startswith("create_post:upstream_rejected"))
            plugin._store.close()

    def test_readback_failure_keeps_link_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            client = _Client(
                create_result={"link_id": "L9"},
                context_error=XiaoheiheApiError("forbidden", status_code=403, category="forbidden"),
            )
            asyncio.run(plugin._create_proactive_post(client))
            row = plugin._store.list_outgoing()[0]
            self.assertEqual(row["status"], OutgoingStatus.SEND_UNKNOWN.value)
            self.assertEqual(row["external_id"], "L9")
            self.assertEqual(row["metadata"]["link_id"], "L9")
            self.assertTrue(row["error"].startswith("readback:"))
            plugin._store.close()

    def test_missing_link_id_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            asyncio.run(plugin._create_proactive_post(_Client(create_result={})))
            row = plugin._store.list_outgoing()[0]
            self.assertEqual(row["status"], OutgoingStatus.SEND_UNKNOWN.value)
            self.assertEqual(row["error"], "missing_link_id")
            plugin._store.close()

    def test_reconcile_verifies_post_by_stored_link_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            action = OutgoingAction(
                "post:abc",
                "proactive_post",
                "",
                "测试正文内容",
                metadata={
                    "title": "测试标题",
                    "body": "测试正文内容",
                    "link_id": "L77",
                    "image_required": False,
                },
            )
            plugin._store.begin_outgoing(action)
            plugin._store.update_outgoing(
                "post:abc", OutgoingStatus.SEND_UNKNOWN, error="readback:create_post:XiaoheiheApiError"
            )
            client = _Client(
                context=SimpleNamespace(
                    title="测试标题", body="测试正文内容", image_urls=[]
                )
            )
            asyncio.run(plugin._reconcile_unknowns(client))
            row = plugin._store.get_outgoing("post:abc")
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            self.assertEqual(row["external_id"], "L77")
            plugin._store.close()


if __name__ == "__main__":
    unittest.main()

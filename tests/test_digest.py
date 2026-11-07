"""浏览总结发帖：素材、触发、长文发布、重试/跳过。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from content_generator import ContentGenerator
from models import OutgoingAction, OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class


class _Logger:
    def _add(self, *args, **kwargs):
        pass

    info = warning = error = debug = _add


class _Generator:
    def __init__(self):
        self.materials = None

    async def digest(self, materials):
        self.materials = materials
        return "今天的浏览感想", ["第一段内容写在这里。", "第二段内容收个尾。"]


class _Client:
    def __init__(self):
        self.posts = []

    async def resolve_topic_names(self, names):
        return {name: "1001" for name in names}

    async def create_post(self, **kwargs):
        self.posts.append(kwargs)
        return {"link_id": "L100"}

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        return SimpleNamespace(
            title="今天的浏览感想",
            body="第一段内容写在这里。\n第二段内容收个尾。",
            image_urls=[],
        )


class _Runtime:
    credentials = object()

    def __init__(self, client):
        self._client = client

    async def client(self):
        return self._client


class DigestStoreTests(unittest.TestCase):
    def test_materials_and_count_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorStore(Path(tmp) / "state.db")
            self.assertTrue(store.record_digest_material("p1", title="帖子一"))
            self.assertFalse(store.record_digest_material("p1", title="帖子一改"))
            store.record_digest_material("p2", title="帖子二")
            self.assertEqual(store.count_digest_pending(), 2)
            self.assertEqual(store.claim_digest(trigger="count", every_posts=5, interval_hours=24), "")
            batch = store.claim_digest(trigger="count", every_posts=2, interval_hours=24)
            self.assertTrue(batch)
            self.assertEqual([row["post_id"] for row in store.digest_materials(batch)], ["p1", "p2"])
            self.assertEqual(store.count_digest_pending(), 0)
            store.close()

    def test_interval_trigger_and_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorStore(Path(tmp) / "state.db")
            store.record_digest_material("p1", title="帖子一")
            self.assertEqual(store.claim_digest(trigger="interval", every_posts=10, interval_hours=24), "")
            batch = store.claim_digest(trigger="interval", every_posts=10, interval_hours=24, force=True)
            self.assertTrue(batch)
            store.update_digest_run(batch, "sent", link_id="L1")
            self.assertEqual(store.latest_digest_run()["state"], "sent")
            store.close()


class DigestPromptTests(unittest.TestCase):
    def test_generator_returns_title_and_paragraphs(self):
        class _LLM:
            def __init__(self):
                self.prompt = None

            async def generate(self, prompt, **kwargs):
                self.prompt = prompt
                return {
                    "success": True,
                    "response": json.dumps(
                        {"title": "标题", "paragraphs": ["段落一", "段落二"]},
                        ensure_ascii=False,
                    ),
                }

        llm = _LLM()
        generator = ContentGenerator(llm, persona=lambda: "你是一个普通玩家。")
        result = asyncio.run(
            generator.digest(
                [
                    {
                        "title": "某游戏更新",
                        "community": "PC游戏",
                        "excerpt": "更新了不少内容",
                        "my_comments": ["我觉得还行"],
                    }
                ]
            )
        )
        self.assertEqual(result, ("标题", ["段落一", "段落二"]))
        prompt = json.dumps(llm.prompt, ensure_ascii=False)
        self.assertIn("某游戏更新", prompt)
        self.assertIn("我觉得还行", prompt)
        self.assertIn("200–400", prompt)


class DigestPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(self, directory: Path, *, mode="auto", enabled=True):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.basic.mode = mode
        plugin.config.automation.post_target_primary = "PC游戏"
        plugin.config.automation.summary_enabled = enabled
        plugin.config.automation.summary_trigger = "count"
        plugin.config.automation.summary_every_posts = 2
        plugin.config.content.post_summarize_feed = False
        plugin._store = OperatorStore(directory / "state.db")
        plugin._generator = _Generator()
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def _seed(self, plugin):
        plugin._store.record_digest_material("p1", title="帖子一", excerpt="摘要一", community="PC游戏")
        plugin._store.record_digest_material("p2", title="帖子二", excerpt="摘要二", community="盒友杂谈")
        action = OutgoingAction(
            "reply:e1", "notification_reply", "p1", "我的评论", metadata={"post_id": "p1"}
        )
        plugin._store.begin_outgoing(action)
        plugin._store.update_outgoing("reply:e1", OutgoingStatus.VERIFIED)

    def test_publishes_multi_block_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin)
            client = _Client()
            plugin._runtime = _Runtime(client)
            asyncio.run(plugin._maybe_publish_digest(client, force=True))

            self.assertEqual(len(client.posts), 1)
            self.assertEqual(
                client.posts[0]["content_blocks"],
                [{"type": "text", "text": "第一段内容写在这里。"}, {"type": "text", "text": "第二段内容收个尾。"}],
            )
            latest = plugin._store.latest_digest_run()
            self.assertEqual(latest["state"], "sent")
            self.assertEqual(latest["link_id"], "L100")
            self.assertEqual(plugin._generator.materials[0]["my_comments"], ["我的评论"])
            plugin._store.close()

    def test_dry_run_only_stores_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), mode="dry_run")
            self._seed(plugin)
            client = _Client()
            plugin._runtime = _Runtime(client)
            asyncio.run(plugin._maybe_publish_digest(client, force=True))
            self.assertEqual(client.posts, [])
            latest = plugin._store.latest_digest_run()
            self.assertEqual(latest["state"], "ready")
            self.assertEqual(latest["link_id"], "")
            plugin._store.close()

    def test_retry_and_skip_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin)
            batch = plugin._store.claim_digest(
                trigger="count", every_posts=2, interval_hours=24, force=True
            )
            plugin._store.update_digest_run(batch, "failed", error="upstream_rejected")
            ok, text, _ = asyncio.run(plugin.handle_summary_retry_command())
            self.assertTrue(ok)
            self.assertEqual(plugin._store.latest_digest_run()["state"], "retry")
            ok, text, _ = asyncio.run(plugin.handle_summary_skip_command())
            self.assertTrue(ok)
            self.assertEqual(plugin._store.latest_digest_run()["state"], "discarded")
            plugin._store.close()

    def test_summary_status_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin)
            ok, text, _ = asyncio.run(plugin.handle_summary_command())
            self.assertTrue(ok)
            self.assertIn("待总结素材：2 帖", text)
            plugin._store.close()


if __name__ == "__main__":
    unittest.main()

"""WebUI 仪表盘：快照结构、动作接口、页面与配置一致性。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dm_store import DirectMessageStore
from models import OutgoingAction, OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def _add(self, *args, **kwargs):
        pass

    info = warning = error = debug = _add


class _Runtime:
    def __init__(self, credentials=None):
        self.credentials = credentials


class WebUIDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(self, directory: Path, *, logged_in: bool = True):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.basic.mode = "auto"
        plugin._store = OperatorStore(directory / "state.db")
        plugin._dm_store = DirectMessageStore(directory / "state.db")
        plugin._runtime = _Runtime(
            SimpleNamespace(uid="42", nickname="盒友") if logged_in else None
        )
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def _seed(self, plugin, key, kind, status, *, error="", content="内容"):
        action = OutgoingAction(key, kind, "100", content, metadata={"post_id": "100"})
        plugin._store.begin_outgoing(action)
        plugin._store.update_outgoing(key, status, error=error)

    def test_dashboard_payload_counts_and_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin, "reply:e1", "notification_reply", OutgoingStatus.VERIFIED)
            self._seed(
                plugin,
                "feed_comment:200",
                "proactive_comment",
                OutgoingStatus.FAILED,
                error="upstream_rejected",
                content="这条评论失败了",
            )
            self._seed(plugin, "reply:e2", "notification_reply", OutgoingStatus.SUPPRESSED, error="cooldown")

            payload = plugin._dashboard_payload()

            self.assertEqual(payload["plugin"]["mode"], "auto")
            self.assertTrue(payload["account"]["logged_in"])
            self.assertEqual(payload["account"]["uid"], "42")
            self.assertEqual(payload["counters"]["verified"], 1)
            self.assertEqual(payload["counters"]["failed"], 1)
            self.assertEqual(payload["counters"]["suppressed"], 1)
            self.assertEqual(payload["today"]["replies"], 1)
            self.assertEqual(payload["today"]["comments"], 0)
            self.assertEqual(len(payload["recent"]), 3)
            failed = [item for item in payload["recent"] if item["status"] == "failed"][0]
            self.assertEqual(failed["kind"], "proactive_comment")
            self.assertEqual(failed["post_id"], "100")
            self.assertIn("这条评论失败了", failed["preview"])
            self.assertEqual(failed["error"], "upstream_rejected")
            self.assertIn("limits", payload)
            self.assertIn("direct_message", payload)
            plugin._store.close()
            plugin._dm_store.close()

    def test_retry_action_requeues_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            self._seed(plugin, "reply:e1", "notification_reply", OutgoingStatus.FAILED)
            payload = asyncio.run(plugin.api_webui_retry_failed())
            self.assertEqual(payload["action_result"]["count"], 1)
            self.assertTrue(payload["action_result"]["ok"])
            self.assertEqual(
                plugin._store.get_outgoing("reply:e1")["status"],
                OutgoingStatus.SUPPRESSED.value,
            )
            plugin._store.close()
            plugin._dm_store.close()

    def test_check_action_runs_inbox_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp))
            calls = []

            async def fake_check():
                calls.append(1)

            plugin._check_inbox_once = fake_check
            payload = asyncio.run(plugin.api_webui_check_now())
            self.assertTrue(payload["action_result"]["ok"])
            self.assertEqual(calls, [1])
            plugin._store.close()
            plugin._dm_store.close()

    def test_check_action_requires_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._plugin(Path(tmp), logged_in=False)
            payload = asyncio.run(plugin.api_webui_check_now())
            self.assertFalse(payload["action_result"]["ok"])
            self.assertIn("登录", payload["action_result"]["message"])
            plugin._store.close()
            plugin._dm_store.close()

    def test_webui_config_declares_dashboard(self):
        config = json.loads((PLUGIN_ROOT / "webui.json").read_text(encoding="utf-8"))
        pages = {page["id"]: page for page in config["pages"]}
        self.assertIn("dashboard", pages)
        dashboard = pages["dashboard"]
        self.assertEqual(dashboard["entry"], "webui/dashboard.html")
        self.assertEqual(dashboard["queries"]["overview"]["api"], "webui_dashboard")
        self.assertEqual(dashboard["actions"]["check_now"]["api"], "webui_check_now")
        self.assertEqual(dashboard["actions"]["retry_failed"]["api"], "webui_retry_failed")
        self.assertTrue((PLUGIN_ROOT / "webui" / "dashboard.html").exists())

    def test_dashboard_html_uses_bridge(self):
        html = (PLUGIN_ROOT / "webui" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("maibot.query('overview')", html)
        self.assertIn("maibot.action(name, {})", html)
        self.assertIn("runAction('check_now')", html)
        self.assertIn("runAction('retry_failed')", html)


if __name__ == "__main__":
    unittest.main()

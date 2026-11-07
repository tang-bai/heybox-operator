"""旧配置迁移：上游的「发帖目标 1/2」→ 二创的「发帖社区 + 话题标签」。

上游允许的值（Steam、PC 游戏、盒友日常…）已经在二创的 Literal 下拉框里被收窄，
如果原样交给 Pydantic 会直接判非法、整份配置加载失败。这里验证降级逻辑：
迁移后旧值落到兼容字段，发送前仍能按名字解析，不会被悄悄丢掉。
"""

from __future__ import annotations

import unittest

from test_notification_retry import _load_plugin_class


class LegacyConfigMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _normalize(self, payload: dict) -> dict:
        plugin = self.plugin_type()
        data, _changed = plugin.normalize_plugin_config(payload)
        self.assertIsInstance(data, dict)
        return data

    def test_upstream_community_names_are_demoted_not_dropped(self):
        data = self._normalize(
            {"automation": {"post_target_primary": "Steam", "post_target_secondary": "PC 游戏"}}
        )
        automation = data["automation"]
        self.assertEqual(automation["post_target_primary"], "")
        self.assertEqual(automation["post_target_secondary"], "")
        # 发帖社区降级到兼容字段，仍会由 resolve_topic_names 解析成真实 topic_id。
        self.assertEqual(automation["post_topic_ids"], ["Steam"])
        # 旧的「目标 2」是社区名，不是话题标签，转成自定义标签保留下来。
        self.assertIn("PC 游戏", automation["custom_hashtags"])

    def test_supported_values_survive_unchanged(self):
        data = self._normalize(
            {"automation": {"post_target_primary": "PC游戏", "post_target_secondary": "盒友日常"}}
        )
        automation = data["automation"]
        self.assertEqual(automation["post_target_primary"], "PC游戏")
        self.assertEqual(automation["post_target_secondary"], "盒友日常")
        self.assertEqual(automation.get("post_topic_ids", []), [])

    def test_flat_legacy_plugin_fields_still_migrate(self):
        data = self._normalize(
            {"plugin": {"mode": "dry_run", "posts_per_day": 0, "post_topic_ids": "1001，1002"}}
        )
        self.assertEqual(data["basic"]["mode"], "dry_run")
        self.assertEqual(data["automation"]["posts_per_day"], 0)
        self.assertEqual(data["automation"]["post_topic_ids"], ["1001", "1002"])
        self.assertNotIn("mode", data["plugin"])

    def test_numeric_legacy_targets_are_kept_as_is(self):
        data = self._normalize(
            {"automation": {"post_target_primary": "", "post_topic_ids": ["1001"]}}
        )
        automation = data["automation"]
        self.assertEqual(automation["post_topic_ids"], ["1001"])
        self.assertEqual(automation["post_target_primary"], "")


if __name__ == "__main__":
    unittest.main()

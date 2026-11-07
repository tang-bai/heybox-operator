"""表情规范化：已知别名转标准标识符，未知表情丢弃，非表情方括号保留。"""

from __future__ import annotations

import unittest

from emoji_catalog import canonical_emoji_token, normalize_outgoing_text


class EmojiNormalizationTests(unittest.TestCase):
    def test_known_alias_is_mapped(self):
        self.assertEqual(normalize_outgoing_text("[cube_狗头] 今天好热"), "[cube_doge] 今天好热")

    def test_standard_name_is_kept(self):
        self.assertEqual(normalize_outgoing_text("[cube_吃瓜]"), "[cube_吃瓜]")

    def test_prefix_alias_is_mapped(self):
        self.assertEqual(normalize_outgoing_text("笑死[表情:笑哭]"), "笑死[cube_笑哭]")
        self.assertEqual(normalize_outgoing_text("[emoji:doge]"), "[cube_doge]")

    def test_unknown_emoji_is_dropped(self):
        self.assertEqual(normalize_outgoing_text("[cube_不存在的表情] 正文"), "正文")

    def test_non_emoji_brackets_survive(self):
        self.assertEqual(normalize_outgoing_text("[链接_1] 正常文本"), "[链接_1] 正常文本")
        self.assertEqual(normalize_outgoing_text("[图片] 说明"), "[图片] 说明")

    def test_empty_and_plain_text(self):
        self.assertEqual(normalize_outgoing_text(""), "")
        self.assertEqual(normalize_outgoing_text("没有表情"), "没有表情")

    def test_canonical_token_helper(self):
        self.assertEqual(canonical_emoji_token("狗头"), "cube_doge")
        self.assertEqual(canonical_emoji_token("cube_doge"), "cube_doge")
        self.assertEqual(canonical_emoji_token("bigemoji_滑稽"), "bigemoji_滑稽")
        self.assertEqual(canonical_emoji_token("表情:求带"), "bigemoji_求带")


if __name__ == "__main__":
    unittest.main()

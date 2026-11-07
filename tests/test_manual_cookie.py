"""手动 Cookie 登录：解析、保存、复用、拒绝无效输入。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from xiaoheihe_runtime import XiaoheiheRuntime


class ManualCookieTests(unittest.TestCase):
    def test_cookie_is_saved_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def run():
                runtime = XiaoheiheRuntime(Path(tmp), "default")
                credentials, message = await runtime.apply_manual_cookie(
                    "user_pkey=pk-value; heybox_id=42; nickname=盒友"
                )
                self.assertEqual(message, "ok")
                self.assertIsNotNone(credentials)
                self.assertEqual(credentials.uid, "42")
                self.assertEqual(credentials.nickname, "盒友")
                stored = json.loads(
                    (Path(tmp) / "credentials" / "default.json").read_text(encoding="utf-8")
                )
                self.assertEqual(stored["cookies"]["user_pkey"], "pk-value")
                self.assertIn("x_xhh_tokenid", stored["cookies"])

                reopened = XiaoheiheRuntime(Path(tmp), "default")
                self.assertIsNotNone(reopened.credentials)
                self.assertEqual(reopened.credentials.uid, "42")
                await runtime.close()

            asyncio.run(run())

    def test_multiline_cookie_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def run():
                runtime = XiaoheiheRuntime(Path(tmp), "default")
                credentials, _ = await runtime.apply_manual_cookie(
                    "user_pkey=pk\nheybox_id=7\nnickname=小盒"
                )
                self.assertIsNotNone(credentials)
                self.assertEqual(credentials.uid, "7")
                await runtime.close()

            asyncio.run(run())

    def test_cookie_without_pkey_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def run():
                runtime = XiaoheiheRuntime(Path(tmp), "default")
                credentials, message = await runtime.apply_manual_cookie("heybox_id=42")
                self.assertIsNone(credentials)
                self.assertIn("user_pkey", message)
                self.assertIsNone(runtime.credentials)
                await runtime.close()

            asyncio.run(run())

    def test_garbage_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def run():
                runtime = XiaoheiheRuntime(Path(tmp), "default")
                credentials, message = await runtime.apply_manual_cookie("随便写点什么")
                self.assertIsNone(credentials)
                self.assertTrue(message)
                await runtime.close()

            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

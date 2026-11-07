"""帖子树读取必须带网页端分页参数，否则只返回评论、读回会误判。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from vendor_xiaoheihe.api_client import XiaoheiheApiClient
from vendor_xiaoheihe.models import Credentials


class _Cookies:
    def __init__(self):
        self.jar = []

    def set(self, name, value):
        self.jar.append(SimpleNamespace(name=name, value=value))


class _Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.cookies = _Cookies()

    def json(self):
        return self.payload


class _Transport:
    def __init__(self):
        self.calls = []
        self.cookies = _Cookies()

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _Response(
            {
                "status": "ok",
                "result": {
                    "link": {"title": "测试帖", "text": "正文"},
                    "comments": [],
                },
            }
        )

    async def aclose(self):
        pass


class PostTreeContractTests(unittest.TestCase):
    def test_post_tree_includes_web_paging_params(self):
        asyncio.run(self._assert_params())

    async def _assert_params(self):
        transport = _Transport()
        client = XiaoheiheApiClient(
            "default",
            credentials=Credentials(
                profile_id="default",
                uid="me",
                nickname="me",
                cookies={"user_pkey": "pk"},
            ),
            client=transport,
            min_request_interval_seconds=0,
        )
        await client.fetch_thread_context("100")
        await client.fetch_thread_context("100", root_comment_id="900")
        first = transport.calls[0][2]["params"]
        second_post = transport.calls[1][2]["params"]
        second_thread = transport.calls[2][2]["params"]
        for params in (first, second_post, second_thread):
            self.assertEqual(params["link_id"], "100")
            self.assertEqual(params["is_first"], "1")
            self.assertEqual(params["page"], "1")
            self.assertEqual(params["index"], "1")
            self.assertEqual(params["limit"], "20")
            self.assertEqual(params["sort_filter"], "time")
            self.assertEqual(params["owner_only"], "0")
        self.assertNotIn("root_comment_id", first)
        self.assertNotIn("root_comment_id", second_post)
        self.assertEqual(second_thread["root_comment_id"], "900")
        await client.close()


if __name__ == "__main__":
    unittest.main()

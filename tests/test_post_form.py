"""发帖表单：link_tag 由分区决定（58144→28，其它→27），desc 非空。"""

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
        return _Response({"status": "ok", "link_id": "12345"})

    async def aclose(self):
        pass


def _client(transport):
    return XiaoheiheApiClient(
        "default",
        credentials=Credentials(
            profile_id="default", uid="me", nickname="me", cookies={"user_pkey": "pk"}
        ),
        client=transport,
        min_request_interval_seconds=0,
    )


class CreatePostFormTests(unittest.TestCase):
    def test_normal_topic_uses_link_tag_27(self):
        asyncio.run(self._assert_tag("1001", "27"))

    def test_default_topic_uses_link_tag_28(self):
        asyncio.run(self._assert_tag("58144", "28"))

    async def _assert_tag(self, topic: str, tag: str):
        transport = _Transport()
        client = _client(transport)
        await client.create_post(
            title="标题",
            content_blocks=[
                {"type": "text", "text": "第一段正文"},
                {"type": "text", "text": "第二段正文"},
            ],
            topic_ids=topic,
            hashtags=["盒友日常"],
        )
        form = transport.calls[0][2]["data"]
        self.assertEqual(form["link_tag"], tag)
        self.assertEqual(form["desc"], "第一段正文")
        self.assertEqual(form["post_type"], "1")
        self.assertIn("topic_ids", form)
        self.assertEqual(form["hashtags"], '["盒友日常"]')
        await client.close()


if __name__ == "__main__":
    unittest.main()

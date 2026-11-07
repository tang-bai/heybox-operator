"""宿主 Replyer 桥接：会话创建、能力调用、失败回退。"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from content_generator import ContentGenerator
from host_replyer import HostReplyer, HostReplyerError

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class _Chat:
    def __init__(self):
        self.calls = []

    async def open_session(self, **kwargs):
        self.calls.append(kwargs)
        return {"stream_id": "stream-1", "platform": kwargs.get("platform")}


class _Ctx:
    def __init__(self, result=None, error=None, with_chat=True):
        self.chat = _Chat() if with_chat else None
        self.calls = []
        self.result = result if result is not None else {"success": True, "text": "来自宿主的文本"}
        self.error = error

    async def call_capability(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


class HostReplyerTests(unittest.TestCase):
    def test_stream_is_created_once_and_text_is_returned(self):
        ctx = _Ctx()
        replyer = HostReplyer(ctx, profile_id="default", account_id="42")
        first = asyncio.run(replyer.generate(reference="材料", format_instruction="只输出正文"))
        second = asyncio.run(replyer.generate(reference="材料2", format_instruction="只输出正文"))
        self.assertEqual(first, "来自宿主的文本")
        self.assertEqual(second, "来自宿主的文本")
        self.assertEqual(len(ctx.chat.calls), 1)
        self.assertEqual(ctx.chat.calls[0]["platform"], "internal")
        self.assertEqual(ctx.chat.calls[0]["group_id"], "xiaoheihe-operator:default")
        name, kwargs = ctx.calls[0]
        self.assertEqual(name, "maisaka.reply.generate")
        self.assertEqual(kwargs["stream_id"], "stream-1")
        self.assertEqual(kwargs["format_instruction"], "只输出正文")

    def test_existing_stream_skips_session_creation(self):
        ctx = _Ctx()
        replyer = HostReplyer(ctx, stream_id="existing-stream")
        asyncio.run(replyer.generate(reference="材料", format_instruction="x"))
        self.assertEqual(ctx.chat.calls, [])
        self.assertEqual(ctx.calls[0][1]["stream_id"], "existing-stream")

    def test_failed_capability_raises(self):
        ctx = _Ctx(result={"success": False, "error": "未找到已存在的聊天流"})
        with self.assertRaises(HostReplyerError):
            asyncio.run(HostReplyer(ctx).generate(reference="材料", format_instruction="x"))

    def test_missing_chat_namespace_raises(self):
        ctx = _Ctx(with_chat=False)
        with self.assertRaises(HostReplyerError):
            asyncio.run(HostReplyer(ctx).ensure_stream())


class _RaisingLLM:
    async def generate(self, *args, **kwargs):
        raise AssertionError("不应调用本地模型")


class _FixedLLM:
    def __init__(self, text):
        self.text = text

    async def generate(self, *args, **kwargs):
        return {"success": True, "response": self.text}


class ContentGeneratorWriterTests(unittest.TestCase):
    def test_reply_uses_host_text(self):
        async def writer(reference, instruction):
            self.assertIn("帖子标题", reference)
            return "宿主写的评论"

        generator = ContentGenerator(_RaisingLLM(), writer=writer)
        result = asyncio.run(generator.reply(post_title="标题", post_body="正文"))
        self.assertEqual(result, "宿主写的评论")

    def test_writer_failure_falls_back_to_local_model(self):
        async def writer(reference, instruction):
            raise RuntimeError("host down")

        generator = ContentGenerator(_FixedLLM("本地回复"), writer=writer)
        result = asyncio.run(generator.reply(post_title="标题", post_body="正文"))
        self.assertEqual(result, "本地回复")

    def test_digest_uses_host_json(self):
        async def writer(reference, instruction):
            return json.dumps({"title": "宿主标题", "paragraphs": ["段一", "段二"]}, ensure_ascii=False)

        generator = ContentGenerator(_RaisingLLM(), writer=writer)
        result = asyncio.run(generator.digest([{"title": "帖子", "excerpt": "摘要"}]))
        self.assertEqual(result, ("宿主标题", ["段一", "段二"]))


class ManifestTests(unittest.TestCase):
    def test_manifest_declares_host_capabilities(self):
        data = json.loads((PLUGIN_ROOT / "_manifest.json").read_text(encoding="utf-8"))
        capabilities = data["capabilities"]
        self.assertIn("chat.open_session", capabilities)
        self.assertIn("maisaka.reply.generate", capabilities)


if __name__ == "__main__":
    unittest.main()

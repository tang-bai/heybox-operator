"""复用宿主 MaiBot 的 Replyer（人格、记忆、Hook）生成文本。

能力来自宿主 1.3.6+ / SDK 2.12+：
- chat.open_session：为插件创建一个真实的聊天流；
- maisaka.reply.generate：用该流的 Replyer 链路写作，结果只返回给插件。
旧宿主缺少这些能力时抛 HostReplyerError，由调用方回退到插件自己的模型。
"""

from __future__ import annotations

from typing import Any


class HostReplyerError(RuntimeError):
    """Host Replyer is unavailable or refused the request."""


class HostReplyer:
    def __init__(
        self,
        ctx: Any,
        *,
        platform: str = "xiaoheihe",
        profile_id: str = "default",
        account_id: str = "",
    ) -> None:
        self.ctx = ctx
        self.platform = platform
        self.profile_id = profile_id
        self.account_id = account_id
        self._stream_id = ""

    async def ensure_stream(self) -> str:
        if self._stream_id:
            return self._stream_id
        chat = getattr(self.ctx, "chat", None)
        open_session = getattr(chat, "open_session", None)
        if not callable(open_session):
            raise HostReplyerError("宿主不支持 chat.open_session")
        session = await open_session(
            platform=self.platform,
            chat_type="group",
            group_id=f"xiaoheihe-operator:{self.profile_id}",
            account_id=self.account_id or None,
        )
        stream_id = ""
        if isinstance(session, dict):
            stream_id = str(session.get("stream_id") or "")
        if not stream_id:
            raise HostReplyerError("chat.open_session 未返回 stream_id")
        self._stream_id = stream_id
        return stream_id

    async def generate(
        self,
        *,
        reference: str,
        format_instruction: str,
        model_name: str = "",
        timeout_ms: int = 180000,
    ) -> str:
        stream_id = await self.ensure_stream()
        call = getattr(self.ctx, "call_capability", None)
        if not callable(call):
            raise HostReplyerError("宿主不支持 call_capability")
        result = await call(
            "maisaka.reply.generate",
            stream_id=stream_id,
            reference=str(reference),
            format_instruction=str(format_instruction),
            model_name=str(model_name or ""),
            timeout_ms=max(1000, int(timeout_ms)),
        )
        if not isinstance(result, dict) or not result.get("success"):
            error = str((result or {}).get("error") or "Replyer 生成失败")
            raise HostReplyerError(error[:300])
        text = str(result.get("text") or "").strip()
        if not text:
            raise HostReplyerError("Replyer 返回空文本")
        return text

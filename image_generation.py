"""直接调用 OpenAI 兼容生图接口（/images/generations）生成帖子配图。

不依赖「麦麦绘图」插件。用户既可以用 MaiBot 已加载的「画图」provider，
也可以在插件配置里单独填一个中转站（base_url / api_key / model）。

接口约定（已实测飞鱼中转站）：
  POST {base_url}/images/generations
  body: {"model": ..., "prompt": ..., "n": 1, "size": "1024x1024"}
  返回: {"data": [{"b64_json": "..."}]} 或 {"data": [{"url": "..."}]}

用 urllib 实现，与插件 vendor_xiaoheihe 保持一致，避免引入额外依赖。

安全边界：生图接口返回的 url 属于外部数据，下载前必须过
vendor_xiaoheihe.security 的 HTTPS / 公网地址校验，并限制文件体积，
避免被中转站或中间人引导去访问本机、内网地址或撑爆内存。
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

try:
    from .vendor_xiaoheihe.security import (
        SecurityError,
        resolve_public_host,
        validate_public_https_url,
    )
except ImportError:  # pragma: no cover - direct Runner fallback
    from vendor_xiaoheihe.security import (  # type: ignore[no-redef]
        SecurityError,
        resolve_public_host,
        validate_public_https_url,
    )


#: 单张配图的体积上限；超过时按「生图失败」处理并降级为纯文字发帖。
MAX_IMAGE_BYTES = 20 * 1024 * 1024
#: base64 文本的上限，留一点余量给 padding 和空白字符。
_MAX_B64_CHARS = (4 * MAX_IMAGE_BYTES // 3) + 64
_GENERATION_TIMEOUT_SECONDS = 300
_DOWNLOAD_TIMEOUT_SECONDS = 180


class ImageGenerationError(RuntimeError):
    """生图失败，调用方应降级为纯文字发帖，而不是抛给上层。"""


class DirectImageGenerator:
    """OpenAI 兼容生图，封装成异步接口，方便在插件里 await。"""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model

    async def generate(self, prompt: str) -> bytes:
        """生成一张图，返回 PNG/JPEG 字节。失败抛 ImageGenerationError。"""

        if not self.base_url or not self.api_key or not self.model:
            raise ImageGenerationError("生图配置不完整：需要 base_url / api_key / model")

        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, self._request_payload, prompt)
        item = _first_image_item(payload)

        encoded = item.get("b64_json")
        if encoded:
            return _decode_b64_image(encoded)

        remote_url = item.get("url")
        if remote_url:
            return await self._download_public_image(str(remote_url))

        raise ImageGenerationError("生图返回里既没有 b64_json 也没有 url")

    def _request_payload(self, prompt: str) -> dict[str, Any]:
        """同步调用生图接口，只负责拿到 JSON，不在这里下载图片。"""

        url = self.base_url + "/images/generations"
        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_GENERATION_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - 只是补充错误信息
                pass
            raise ImageGenerationError(f"生图接口返回 {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ImageGenerationError(f"生图请求失败: {type(exc).__name__}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ImageGenerationError("生图接口返回非 JSON") from exc
        if not isinstance(parsed, dict):
            raise ImageGenerationError("生图接口返回结构不是对象")
        return parsed

    async def _download_public_image(self, url: str) -> bytes:
        """校验并下载生图接口返回的图片地址（HTTPS + 公网 + 体积上限）。"""

        try:
            safe_url = validate_public_https_url(url)
            hostname = urlsplit(safe_url).hostname or ""
            await resolve_public_host(hostname)
        except (SecurityError, OSError, ValueError) as exc:
            raise ImageGenerationError(f"生图图片地址不可用: {exc}") from exc

        def _fetch() -> bytes:
            try:
                with urllib.request.urlopen(safe_url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
                    data = response.read(MAX_IMAGE_BYTES + 1)
            except (urllib.error.URLError, OSError) as exc:
                raise ImageGenerationError(f"下载生成图片失败: {type(exc).__name__}") from exc
            if len(data) > MAX_IMAGE_BYTES:
                raise ImageGenerationError("生成图片超过体积上限")
            if not data:
                raise ImageGenerationError("生成图片内容为空")
            return data

        return await asyncio.get_running_loop().run_in_executor(None, _fetch)


def _first_image_item(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("data")
    if not isinstance(items, list) or not items:
        raise ImageGenerationError(
            f"生图接口没有返回图片数据: {json.dumps(payload, ensure_ascii=False)[:300]}"
        )
    first = items[0]
    if not isinstance(first, dict):
        raise ImageGenerationError("生图返回的图片条目不是对象")
    return first


def _decode_b64_image(encoded: Any) -> bytes:
    text = str(encoded or "")
    if len(text) > _MAX_B64_CHARS:
        raise ImageGenerationError("生成图片的 base64 超过体积上限")
    try:
        data = base64.b64decode(text, validate=True)
    except Exception as exc:  # noqa: BLE001 - binascii.Error 也在这里
        raise ImageGenerationError("图片 base64 解码失败") from exc
    if not data:
        raise ImageGenerationError("生成图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("生成图片超过体积上限")
    return data


def build_generator(base_url: str, api_key: str, model: str) -> DirectImageGenerator | None:
    """三个参数都填了才返回生成器，否则返回 None。"""

    if base_url and api_key and model:
        return DirectImageGenerator(base_url=base_url, api_key=api_key, model=model)
    return None

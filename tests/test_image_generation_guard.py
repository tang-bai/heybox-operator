"""生图下载的安全边界：只允许 HTTPS 公网地址，并且限制图片体积。

生图接口返回的 url 是外部数据，不能直接交给 urlopen：
- http / 本机 / 内网地址一律拒绝（SSRF 防护）；
- 超出 MAX_IMAGE_BYTES 的图片按失败处理，交给上层降级为纯文字发帖。
"""

from __future__ import annotations

import asyncio
import base64
import unittest

from image_generation import (
    MAX_IMAGE_BYTES,
    DirectImageGenerator,
    ImageGenerationError,
    _decode_b64_image,
)


def _generator() -> DirectImageGenerator:
    return DirectImageGenerator(base_url="https://relay.example.com/v1", api_key="test", model="m")


class Base64GuardTests(unittest.TestCase):
    def test_small_image_is_decoded(self):
        raw = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        self.assertEqual(_decode_b64_image(base64.b64encode(raw).decode("ascii")), raw)

    def test_oversized_base64_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            _decode_b64_image("A" * (MAX_IMAGE_BYTES * 2))

    def test_broken_base64_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            _decode_b64_image("not-base64!!")


class DownloadGuardTests(unittest.TestCase):
    def _download(self, url: str) -> bytes:
        return asyncio.run(_generator()._download_public_image(url))

    def test_plain_http_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            self._download("http://relay.example.com/a.png")

    def test_loopback_literal_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            self._download("https://127.0.0.1/a.png")

    def test_private_network_literal_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            self._download("https://192.168.1.10/a.png")

    def test_localhost_name_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            self._download("https://localhost/a.png")

    def test_url_with_credentials_is_rejected(self):
        with self.assertRaises(ImageGenerationError):
            self._download("https://user:pass@relay.example.com/a.png")


if __name__ == "__main__":
    unittest.main()

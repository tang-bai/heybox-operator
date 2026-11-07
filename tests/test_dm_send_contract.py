"""私信发送契约：请求形状、确认字段、限制/认证错误与超时语义。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from vendor_xiaoheihe.api_client import (
    DirectMessageRestrictedError,
    DirectMessageVerificationRequiredError,
    SendUncertainError,
    XiaoheiheApiClient,
)
from vendor_xiaoheihe.endpoints import EndpointName, endpoint
from vendor_xiaoheihe.http_compat import NetworkError
from vendor_xiaoheihe.models import Credentials


class _Cookies:
    def __init__(self):
        self.jar = []

    def set(self, name: str, value: str) -> None:
        self.jar = [cookie for cookie in self.jar if cookie.name != name]
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
    """Returns queued payloads; records every request."""

    def __init__(self, *payloads, fail: bool = False):
        self.payloads = list(payloads)
        self.fail = fail
        self.calls = []
        self.cookies = _Cookies()

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.fail:
            raise NetworkError("offline test failure")
        payload = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        return _Response(payload)

    async def aclose(self):
        pass


def _client(transport: _Transport) -> XiaoheiheApiClient:
    return XiaoheiheApiClient(
        "default",
        credentials=Credentials(
            profile_id="default",
            uid="me-1",
            nickname="me",
            cookies={
                "user_pkey": "pk",
                "heybox_id": "me-1",
                "x_xhh_tokenid": "tk",
                "sensorsdata": "noise",
            },
            device_id="device-1234",
        ),
        client=transport,
        min_request_interval_seconds=0,
    )


ACK_PAYLOAD = {
    "status": "ok",
    "result": {"heychat_ack_id": "9988", "msg_id": "m-1", "msg_seq": "7"},
}


class DirectMessageSendContractTests(unittest.TestCase):
    def test_send_builds_web_chat_request(self):
        asyncio.run(self._assert_send_request())

    async def _assert_send_request(self):
        transport = _Transport(ACK_PAYLOAD)
        client = _client(transport)
        result = await client.send_direct_message(peer_uid="peer-9", text="在吗？")

        self.assertEqual(result.message_id, "m-1")
        self.assertEqual(result.heychat_ack_id, "9988")
        self.assertEqual(len(transport.calls), 1)
        method, path, request = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/chatroom/v2/msg/user")
        self.assertEqual(request["params"]["to_user_id"], "peer-9")
        self.assertEqual(request["params"]["app"], "heybox")
        self.assertNotIn("_notip", request["params"])
        self.assertEqual(request["params"]["device_id"], "device-1234")
        self.assertIn("hkey", request["params"])
        self.assertIn("_time", request["params"])
        self.assertEqual(request["data"]["msg"], "在吗？")
        self.assertEqual(request["data"]["msg_type"], "6")
        self.assertEqual(request["data"]["img"], "")
        self.assertTrue(request["data"]["heybox_ack_id"].isdigit())
        self.assertIn("Chrome/125", request["headers"]["User-Agent"])
        cookie = request["headers"]["Cookie"]
        self.assertIn("user_pkey=pk", cookie)
        self.assertIn("x_xhh_tokenid=tk", cookie)
        self.assertNotIn("sensorsdata", cookie)
        self.assertNotIn("Authorization", request["headers"])
        await client.close()

    def test_ack_id_only_grows(self):
        asyncio.run(self._assert_ack_growth())

    async def _assert_ack_growth(self):
        transport = _Transport(ACK_PAYLOAD)
        client = _client(transport)
        await client.send_direct_message(peer_uid="peer-9", text="一")
        await client.send_direct_message(peer_uid="peer-9", text="二")
        first = int(transport.calls[0][2]["data"]["heybox_ack_id"])
        second = int(transport.calls[1][2]["data"]["heybox_ack_id"])
        self.assertEqual(second, first + 1)
        await client.close()

    def test_missing_ack_is_not_reported_as_sent(self):
        asyncio.run(self._assert_missing_ack())

    async def _assert_missing_ack(self):
        transport = _Transport({"status": "ok", "result": {}})
        client = _client(transport)
        with self.assertRaises(Exception) as caught:
            await client.send_direct_message(peer_uid="peer-9", text="x")
        self.assertEqual(getattr(caught.exception, "category", ""), "send_unconfirmed")
        await client.close()

    def test_web_auth_requirement_is_surfaced(self):
        asyncio.run(self._assert_verification_required())

    async def _assert_verification_required(self):
        payload = {
            "status": "ok",
            "result": {"heybox__protocol__execute__directly": "heybox%3A%2F%2Fweb_auth"},
        }
        client = _client(_Transport(payload))
        with self.assertRaises(DirectMessageVerificationRequiredError):
            await client.send_direct_message(peer_uid="peer-9", text="x")
        await client.close()

    def test_restriction_text_pauses_sending(self):
        asyncio.run(self._assert_restricted())

    async def _assert_restricted(self):
        payload = {"status": "failed", "msg": "私信功能已被限制，请稍后再试", "result": {}}
        client = _client(_Transport(payload))
        with self.assertRaises(DirectMessageRestrictedError) as caught:
            await client.send_direct_message(peer_uid="peer-9", text="x")
        self.assertEqual(caught.exception.category, "dm_restricted")
        await client.close()

    def test_send_timeout_is_uncertain_and_never_retried(self):
        asyncio.run(self._assert_timeout())

    async def _assert_timeout(self):
        transport = _Transport(ACK_PAYLOAD, fail=True)
        client = _client(transport)
        with self.assertRaises(SendUncertainError):
            await client.send_direct_message(peer_uid="peer-9", text="x")
        self.assertEqual(len(transport.calls), 1)
        await client.close()


class DirectMessageReadContractTests(unittest.TestCase):
    def test_conversation_list_uses_user_message_route(self):
        asyncio.run(self._assert_conversation_list())

    async def _assert_conversation_list(self):
        payload = {
            "status": "ok",
            "result": {
                "messages": [
                    {
                        "entry": "message",
                        "user_a": {"userid": "peer-9", "username": "盒友"},
                        "last_message_id": "m-9",
                    },
                    {
                        "entry": "like",
                        "user_a": {"userid": "peer-8", "username": "点赞的人"},
                        "last_message_id": "m-8",
                    },
                ]
            },
        }
        transport = _Transport(payload)
        client = _client(transport)
        page = await client.fetch_direct_conversations(limit=5)
        self.assertEqual([item.peer_uid for item in page.items], ["peer-9"])
        method, path, request = transport.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/bbs/app/user/message")
        self.assertEqual(request["params"]["list_type"], "2")
        await client.close()

    def test_messages_require_peer_id(self):
        asyncio.run(self._assert_messages_require_peer())

    async def _assert_messages_require_peer(self):
        transport = _Transport({"status": "ok", "result": {"list": []}})
        client = _client(transport)
        with self.assertRaises(ValueError):
            await client.fetch_direct_messages(peer_uid="")
        page = await client.fetch_direct_messages(peer_uid="peer-9")
        self.assertEqual(page.items, [])
        method, path, request = transport.calls[0]
        self.assertEqual(path, "/chatroom/v2/msg/user")
        self.assertEqual(request["params"]["to_user_id"], "peer-9")
        await client.close()


class DirectMessageEndpointTests(unittest.TestCase):
    def test_send_endpoint_is_post_and_not_retry_safe(self):
        contract = endpoint(EndpointName.DIRECT_MESSAGE_SEND)
        self.assertEqual(contract.method, "POST")
        self.assertEqual(contract.path, "/chatroom/v2/msg/user")
        self.assertTrue(contract.authenticated)
        self.assertFalse(contract.retry_safe)


if __name__ == "__main__":
    unittest.main()

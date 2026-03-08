import base64

import httpx
import pytest
import respx

from app.services.agent_worker import handle_proxy_request


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.messages.append(payload)


@pytest.mark.asyncio
@respx.mock
async def test_handle_proxy_request_non_stream() -> None:
    sender = FakeSender()
    payload = {
        "type": "proxy_request",
        "request_id": "req-1",
        "method": "POST",
        "url": "https://api.example.com/v1/chat/completions",
        "headers": {"x-test": "1"},
        "body": base64.b64encode(b"{}").decode("utf-8"),
        "stream": False,
    }

    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"})
    )

    async with httpx.AsyncClient() as client:
        await handle_proxy_request(payload, client, sender.send)

    assert sender.messages
    response = sender.messages[0]
    assert response["type"] == "proxy_response"
    assert response["status_code"] == 200
    assert base64.b64decode(response["body"]) == b"ok"


@pytest.mark.asyncio
@respx.mock
async def test_handle_proxy_request_stream() -> None:
    sender = FakeSender()
    payload = {
        "type": "proxy_request",
        "request_id": "req-2",
        "method": "POST",
        "url": "https://api.example.com/v1/chat/completions",
        "headers": {},
        "body": base64.b64encode(b"{}").decode("utf-8"),
        "stream": True,
    }

    respx.post("https://api.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, content=b"chunk", headers={"content-type": "text/event-stream"}
        )
    )

    async with httpx.AsyncClient() as client:
        await handle_proxy_request(payload, client, sender.send)

    assert len(sender.messages) == 3
    assert sender.messages[0]["type"] == "proxy_response"
    assert sender.messages[1]["type"] == "proxy_stream"
    assert base64.b64decode(sender.messages[1]["data"]) == b"chunk"
    assert sender.messages[2]["type"] == "proxy_stream_end"

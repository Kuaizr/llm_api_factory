import base64

import httpx
import pytest
import respx

from app.services.agent_worker import (
    _is_target_allowed,
    _is_target_allowed_for_request,
    handle_proxy_request,
)
from app.core.config import Settings


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.messages.append(payload)


@pytest.mark.asyncio
@respx.mock
async def test_handle_proxy_request_non_stream(monkeypatch: pytest.MonkeyPatch) -> None:
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

    from app.services import agent_worker

    monkeypatch.setattr(
        agent_worker,
        "get_settings",
        lambda: Settings(agent_allowed_targets="api.example.com"),
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
async def test_handle_proxy_request_stream(monkeypatch: pytest.MonkeyPatch) -> None:
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

    from app.services import agent_worker

    monkeypatch.setattr(
        agent_worker,
        "get_settings",
        lambda: Settings(agent_allowed_targets="api.example.com"),
    )
    async with httpx.AsyncClient() as client:
        await handle_proxy_request(payload, client, sender.send)

    assert len(sender.messages) == 3
    assert sender.messages[0]["type"] == "proxy_response"
    assert sender.messages[1]["type"] == "proxy_stream"
    assert base64.b64decode(sender.messages[1]["data"]) == b"chunk"
    assert sender.messages[2]["type"] == "proxy_stream_end"


@pytest.mark.asyncio
@respx.mock
async def test_handle_proxy_request_rejects_disallowed_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = FakeSender()
    payload = {
        "type": "proxy_request",
        "request_id": "req-3",
        "method": "GET",
        "url": "http://169.254.169.254/latest/meta-data",
        "headers": {},
        "body": "",
        "stream": False,
    }

    from app.services import agent_worker

    monkeypatch.setattr(
        agent_worker,
        "get_settings",
        lambda: Settings(agent_allowed_targets="api.example.com"),
    )
    async with httpx.AsyncClient() as client:
        await handle_proxy_request(payload, client, sender.send)

    assert sender.messages
    response = sender.messages[0]
    assert response["status_code"] == 403
    assert base64.b64decode(response["body"]) == b"target_not_allowed"


def test_target_allowlist_wildcard_does_not_allow_restricted_ip_literals() -> None:
    assert _is_target_allowed("https://api.openai.com/v1/models", "*") is True
    assert _is_target_allowed("http://169.254.169.254/latest/meta-data", "*") is False
    assert _is_target_allowed("http://127.0.0.1:9000/v1/models", "*") is False
    assert _is_target_allowed("http://localhost:9000/v1/models", "*") is False


def test_target_allowlist_allows_restricted_targets_when_explicit() -> None:
    assert (
        _is_target_allowed(
            "http://169.254.169.254/latest/meta-data",
            "169.254.169.254",
        )
        is True
    )
    assert _is_target_allowed("http://127.0.0.1:9000/v1/models", "127.0.0.0/8") is True
    assert _is_target_allowed("http://localhost:9000/v1/models", "localhost") is True


@pytest.mark.asyncio
async def test_target_allowlist_wildcard_rejects_hostname_resolving_private() -> None:
    async def parsed_resolver(_host: str, _port: int | None):
        from ipaddress import ip_address

        return [ip_address("10.0.0.5")]

    assert (
        await _is_target_allowed_for_request(
            "https://internal.example.com/v1/models",
            "*",
            resolve_host_ips=parsed_resolver,
        )
        is False
    )


@pytest.mark.asyncio
async def test_target_allowlist_wildcard_allows_hostname_resolving_public() -> None:
    async def parsed_resolver(_host: str, _port: int | None):
        from ipaddress import ip_address

        return [ip_address("93.184.216.34")]

    assert (
        await _is_target_allowed_for_request(
            "https://api.example.com/v1/models",
            "*",
            resolve_host_ips=parsed_resolver,
        )
        is True
    )


@pytest.mark.asyncio
async def test_target_allowlist_explicit_hostname_does_not_resolve_dns() -> None:
    async def failing_resolver(_host: str, _port: int | None):
        raise AssertionError("explicit host should not resolve DNS")

    assert (
        await _is_target_allowed_for_request(
            "https://api.example.com/v1/models",
            "api.example.com",
            resolve_host_ips=failing_resolver,
        )
        is True
    )

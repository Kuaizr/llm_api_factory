from dataclasses import dataclass

import httpx
import pytest

from app.services import endpoint_transport
from app.services.agent_transport import AgentResponse, AgentUnavailableError


@dataclass
class EndpointStub:
    access_mode: str = "direct"
    agent_node: str | None = None


@pytest.mark.asyncio
async def test_direct_endpoint_uses_main_service_http_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"x-source": "main"}, content=b"ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = await endpoint_transport.send_endpoint_request(
        endpoint=EndpointStub(),
        method="GET",
        url="https://upstream.example.test/models",
        headers={"Authorization": "Bearer test"},
        body=b"",
        client=client,
    )
    await client.aclose()

    assert response.status_code == 200
    assert response.body == b"ok"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_agent_endpoint_never_calls_main_service_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAgentManager:
        def __init__(self) -> None:
            self.calls = []

        async def send_request(self, agent_name, request):  # noqa: ANN001
            self.calls.append((agent_name, request))
            return AgentResponse(status_code=200, headers={}, body=b"agent-ok")

    def direct_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("via_agent request must not use the main service network")

    manager = FakeAgentManager()
    monkeypatch.setattr(endpoint_transport, "get_agent_manager", lambda: manager)
    client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))
    response = await endpoint_transport.send_endpoint_request(
        endpoint=EndpointStub(access_mode="via_agent", agent_node="edge-hk"),
        method="POST",
        url="https://auth.example.test/oauth/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=b"grant_type=refresh_token",
        client=client,
    )
    await client.aclose()

    assert response.body == b"agent-ok"
    assert len(manager.calls) == 1
    agent_name, request = manager.calls[0]
    assert agent_name == "edge-hk"
    assert request.url == "https://auth.example.test/oauth/token"
    assert request.body == b"grant_type=refresh_token"


@pytest.mark.asyncio
async def test_agent_endpoint_without_agent_name_does_not_fall_back_to_direct() -> None:
    direct_called = False

    def direct_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal direct_called
        direct_called = True
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))
    with pytest.raises(AgentUnavailableError, match="agent_node is missing"):
        await endpoint_transport.send_endpoint_request(
            endpoint=EndpointStub(access_mode="via_agent"),
            method="GET",
            url="https://upstream.example.test/models",
            headers={},
            body=b"",
            client=client,
        )
    await client.aclose()

    assert direct_called is False

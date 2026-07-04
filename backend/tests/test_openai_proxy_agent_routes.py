import asyncio
import json

import httpx
import pytest

from app.services.agent_transport import AgentResponse, AgentUnavailableError
from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


class UnavailableAgentManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_request(self, agent_name: str, request) -> object:  # noqa: ANN001
        self.calls.append(agent_name)
        raise AgentUnavailableError("agent offline")


class CapturingAgentManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def send_request(self, agent_name: str, request) -> object:  # noqa: ANN001
        self.calls.append((agent_name, request))
        return AgentResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=json.dumps(
                {
                    "id": "cmpl-agent-ok",
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 7,
                    },
                }
            ).encode("utf-8"),
        )


@pytest.mark.asyncio
async def test_proxy_uses_agent_transport_when_endpoint_has_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=24,
        name="AgentPrimary",
        base_url="https://api.example.com",
        agent_node="agent-west",
    )
    api_key = APIKeyStub(id=25, key="sk-agent")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP upstream should not be called for agent endpoint")

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = CapturingAgentManager()
    app = build_proxy_app(
        monkeypatch,
        candidate,
        upstream_client,
        recorded,
        agent_manager=manager,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-agent-ok"
    assert response.headers["x-execution-mode"] == "via_agent"
    assert response.headers["x-agent-node"] == "agent-west"
    assert len(manager.calls) == 1
    agent_name, agent_request = manager.calls[0]
    assert agent_name == "agent-west"
    assert agent_request.url == "https://api.example.com/v1/chat/completions"

    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.execution_mode == "via_agent"
    assert metrics.agent_node == "agent-west"
    assert metrics.upstream_url == "https://api.example.com/v1/chat/completions"


@pytest.mark.asyncio
async def test_proxy_falls_back_when_agent_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_endpoint = EndpointStub(
        id=20,
        name="AgentPrimary",
        base_url="https://api.example.com",
        agent_node="agent-west",
    )
    http_endpoint = EndpointStub(id=21, name="HttpFallback", base_url="https://api.example.com")
    agent_key = APIKeyStub(id=22, key="sk-agent")
    http_key = APIKeyStub(id=23, key="sk-http")
    agent_candidate = RouteCandidate(
        api_key=agent_key,
        endpoint=agent_endpoint,
        real_model="gpt-4o",
    )
    http_candidate = RouteCandidate(
        api_key=http_key,
        endpoint=http_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "cmpl-http-fallback"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = UnavailableAgentManager()
    app = build_proxy_app(
        monkeypatch,
        [agent_candidate, http_candidate],
        upstream_client,
        recorded,
        agent_manager=manager,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-http-fallback"
    assert manager.calls == ["agent-west", "agent-west", "agent-west"]
    assert len(requests) == 1
    assert response.headers["x-endpoint-id"] == str(http_endpoint.id)


@pytest.mark.asyncio
async def test_proxy_returns_502_when_last_agent_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=30,
        name="AgentOnly",
        base_url="https://api.example.com",
        agent_node="agent-only",
    )
    api_key = APIKeyStub(id=31, key="sk-agent-only")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP upstream should not be called for unavailable agent")

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = UnavailableAgentManager()
    app = build_proxy_app(
        monkeypatch,
        candidate,
        upstream_client,
        recorded,
        agent_manager=manager,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 502
    assert response.json()["detail"] == "Agent unavailable"
    assert manager.calls == ["agent-only", "agent-only", "agent-only"]

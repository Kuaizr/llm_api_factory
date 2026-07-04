import asyncio

import httpx
import pytest

from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


@pytest.mark.asyncio
async def test_proxy_preserves_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=6, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=7, key="sk-trace")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "cmpl-2"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Trace-Id": "trace-123"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "trace-123"
    assert requests[0].headers.get("x-trace-id") == "trace-123"
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.trace_id == "trace-123"


@pytest.mark.asyncio
async def test_proxy_uses_session_id_as_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=61, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=62, key="sk-trace")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "cmpl-session-trace"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Session-Id": "session-abc"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "session-abc"
    assert requests[0].headers.get("x-trace-id") == "session-abc"
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.trace_id == "session-abc"

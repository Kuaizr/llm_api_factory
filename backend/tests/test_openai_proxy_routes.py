from dataclasses import dataclass
import json
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.router import RouteCandidate


@dataclass
class EndpointStub:
    id: int
    name: str
    base_url: str
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    agent_node: str | None = None


@dataclass
class APIKeyStub:
    id: int
    key: str
    weight: int = 1


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)

    async def delete(self, key: str) -> bool:
        self.store.pop(key, None)
        self.expirations.pop(key, None)
        return True


class FakeSession:
    async def execute(self, stmt):  # noqa: ANN001
        raise AssertionError("Database should not be used in proxy route tests")


def build_proxy_app(
    monkeypatch: pytest.MonkeyPatch,
    candidate: RouteCandidate,
    upstream_client: httpx.AsyncClient,
    recorded: dict,
) -> FastAPI:
    settings = Settings(master_auth_token="token")
    redis = MemoryRedis()

    async def fake_get_redis():
        return redis

    async def override_session():
        yield FakeSession()

    async def fake_get_candidates(self, session, model_alias: str, rule_group: str):  # noqa: ANN001
        recorded["model_alias"] = model_alias
        recorded["rule_group"] = rule_group
        return [candidate]

    async def fake_get_http_client() -> httpx.AsyncClient:
        return upstream_client

    async def fake_write_request_log(metrics):  # noqa: ANN001
        recorded["metrics"] = metrics

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(routes_module, "get_notifier", lambda: None)
    monkeypatch.setattr(routes_module.ModelRouter, "get_candidates", fake_get_candidates)
    monkeypatch.setattr(routes_module, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(routes_module, "write_request_log", fake_write_request_log)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session
    return app


@pytest.mark.asyncio
async def test_completions_proxy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=1, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=2, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {
        "id": "cmpl-1",
        "object": "text_completion",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4o-mini"
    assert recorded["rule_group"] == "default"
    assert response.headers["x-real-model"] == "gpt-4o"
    assert requests
    sent_request = requests[0]
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_embeddings_proxy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=3, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=4, key="sk-embed")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="text-embedding-3")

    upstream_payload = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/embeddings",
            headers={"Authorization": "Bearer token"},
            json={"model": "text-embedding-small", "input": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert response.headers["x-real-model"] == "text-embedding-3"
    assert recorded["model_alias"] == "text-embedding-small"
    assert requests
    sent_request = requests[0]
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "text-embedding-3"


@pytest.mark.asyncio
async def test_embeddings_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=5, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=6, key="sk-embed")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="text-embedding-3")

    recorded: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Upstream should not be called")

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/embeddings",
            headers={"Authorization": "Bearer token"},
            json={"input": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing model field"


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
            "/v1/completions",
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

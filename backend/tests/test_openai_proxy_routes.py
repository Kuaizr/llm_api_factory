from dataclasses import dataclass
import json
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.agent_transport import AgentUnavailableError
from app.services.router import RouteCandidate


@dataclass
class EndpointStub:
    id: int
    name: str
    base_url: str
    provider: str = "openai"
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    agent_node: str | None = None
    oauth_config: str | None = None
    request_body_template: str | None = None


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
    candidate_or_candidates: RouteCandidate | list[RouteCandidate],
    upstream_client: httpx.AsyncClient,
    recorded: dict,
    *,
    agent_manager: object | None = None,
) -> FastAPI:
    settings = Settings(master_auth_token="token")
    redis = MemoryRedis()
    candidates = (
        list(candidate_or_candidates)
        if isinstance(candidate_or_candidates, list)
        else [candidate_or_candidates]
    )

    async def fake_get_redis():
        return redis

    async def override_session():
        yield FakeSession()

    async def fake_get_candidates(self, session, model_alias: str, rule_group: str):  # noqa: ANN001
        recorded["model_alias"] = model_alias
        recorded["rule_group"] = rule_group
        return candidates, rule_group

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
    if agent_manager is not None:
        monkeypatch.setattr(routes_module, "get_agent_manager", lambda: agent_manager)

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
            "/openai/v1/completions",
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
async def test_completions_proxy_accepts_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=7, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=8, key="sk-test-x")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"id": "cmpl-x-key", "object": "text_completion"}
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
            "/openai/v1/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "prompt": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["rule_group"] == "default"
    assert requests
    sent_request = requests[0]
    assert sent_request.headers.get("authorization") == "Bearer sk-test-x"
    assert sent_request.headers.get("x-api-key") is None


@pytest.mark.asyncio
async def test_openai_standard_passthrough_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=9, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=10, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"id": "chatcmpl-openai", "object": "chat.completion"}
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
            "/openai/v1/chat/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4o-mini"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/chat/completions"
    assert sent_request.headers.get("authorization") == "Bearer sk-openai"
    assert sent_request.headers.get("x-api-key") is None
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_legacy_v1_routes_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=29, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=30, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "should-not-hit"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "legacy"},
        )

    await upstream_client.aclose()

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_openai_standard_models_passthrough_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=15, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=16, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"object": "list", "data": [{"id": "gpt-4o"}]}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/openai/v1/models",
            headers={"x-api-key": "token"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "/openai/v1/models"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/models"
    assert sent_request.content == b""


@pytest.mark.asyncio
async def test_openai_standard_passthrough_accepts_custom_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_endpoint = EndpointStub(
        id=17,
        name="CustomGateway",
        base_url="https://custom.example.com",
        provider="custom",
        request_body_template=json.dumps(
            {
                "model": "{{model}}",
                "prompt": "{{prompt}}",
                "source": "custom",
            }
        ),
    )
    openai_endpoint = EndpointStub(id=18, name="OpenAI", base_url="https://api.example.com")

    custom_candidate = RouteCandidate(
        api_key=APIKeyStub(id=19, key="sk-custom"),
        endpoint=custom_endpoint,
        real_model="vendor/custom-model",
    )
    openai_candidate = RouteCandidate(
        api_key=APIKeyStub(id=20, key="sk-openai"),
        endpoint=openai_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [custom_candidate, openai_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "prompt": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.headers.get("authorization") == "Bearer sk-custom"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "vendor/custom-model"
    assert payload["prompt"] == "hello"
    assert payload["source"] == "custom"


@pytest.mark.asyncio
async def test_anthropic_standard_passthrough_filters_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_endpoint = EndpointStub(id=11, name="OpenAI", base_url="https://api.example.com")
    anthropic_endpoint = EndpointStub(
        id=12,
        name="Anthropic",
        base_url="https://api.anthropic.com",
        provider="anthropic",
    )
    openai_candidate = RouteCandidate(
        api_key=APIKeyStub(id=13, key="sk-openai"),
        endpoint=openai_endpoint,
        real_model="gpt-4o",
    )
    anthropic_candidate = RouteCandidate(
        api_key=APIKeyStub(id=14, key="sk-anthropic"),
        endpoint=anthropic_endpoint,
        real_model="claude-3-5-sonnet",
    )

    upstream_payload = {"id": "msg_1", "type": "message"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [openai_candidate, anthropic_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/anthropic/v1/messages",
            headers={"x-api-key": "token"},
            json={"max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/messages"
    assert sent_request.headers.get("authorization") == "Bearer sk-anthropic"
    assert sent_request.headers.get("x-api-key") is None
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload == {"max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_anthropic_standard_passthrough_falls_back_to_openai_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=21, name="Gateway", base_url="https://api.example.com")
    api_key = APIKeyStub(id=22, key="sk-openai")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="minimax/minimax-m2.5",
    )

    upstream_payload = {"id": "msg_fallback", "type": "message"}
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
            "/anthropic/v1/messages",
            headers={"x-api-key": "token"},
            json={"model": "minimax-m2.5", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "minimax-m2.5"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/messages"
    assert sent_request.headers.get("authorization") == "Bearer sk-openai"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "minimax/minimax-m2.5"


@pytest.mark.asyncio
async def test_responses_proxy_passthrough_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=2, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=3, key="sk-resp")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"id": "resp-1", "object": "response", "output": []}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    raw_payload = {
        "model": "gpt-4o-mini",
        "input": [{"role": "user", "content": "hello"}],
        "temperature": 0.1,
        "rule_group": "qiniu",
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token"},
            json=raw_payload,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4o-mini"
    assert recorded["rule_group"] == "qiniu"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/responses"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o"
    assert payload["input"] == raw_payload["input"]
    assert payload["temperature"] == raw_payload["temperature"]


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
            "/openai/v1/embeddings",
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
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="text-embedding-fallback")

    upstream_payload = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.2]}],
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
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
            "/openai/v1/embeddings",
            headers={"Authorization": "Bearer token"},
            json={"input": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert response.headers["x-real-model"] == "text-embedding-fallback"


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


@pytest.mark.asyncio
async def test_proxy_retries_next_candidate_on_retryable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=10, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=11, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=12, key="sk-primary")
    fallback_key = APIKeyStub(id=13, key="sk-fallback")
    primary_candidate = RouteCandidate(
        api_key=primary_key,
        endpoint=primary_endpoint,
        real_model="gpt-4o",
    )
    fallback_candidate = RouteCandidate(
        api_key=fallback_key,
        endpoint=fallback_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "Bearer sk-primary":
            return httpx.Response(500, json={"error": "retry next candidate"})
        return httpx.Response(200, json={"id": "cmpl-retry-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [primary_candidate, fallback_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-retry-ok"
    assert len(requests) == 2
    assert response.headers["x-api-key-id"] == str(fallback_key.id)


class UnavailableAgentManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_request(self, agent_name: str, request) -> object:  # noqa: ANN001
        self.calls.append(agent_name)
        raise AgentUnavailableError("agent offline")


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
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-http-fallback"
    assert manager.calls == ["agent-west"]
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
    assert manager.calls == ["agent-only"]


@pytest.mark.asyncio
async def test_proxy_stream_path_records_usage_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=40, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=41, key="sk-stream")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    stream_payload = (
        b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
        b'data: {"usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6}}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer token"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )

    await upstream_client.aclose()
    for _ in range(5):
        if recorded.get("metrics") is not None:
            break
        await asyncio.sleep(0)

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.prompt_tokens == 2
    assert metrics.completion_tokens == 4
    assert metrics.total_tokens == 6
    assert metrics.ttft_ms is not None


@pytest.mark.asyncio
async def test_oauth_injects_access_token_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth_config = json.dumps({
        "token_url": "https://auth.example.com/oauth/token",
        "client_id": "test-client",
        "client_secret": "test-secret",
    })
    endpoint = EndpointStub(
        id=100,
        name="OAuthEndpoint",
        base_url="https://api.example.com",
        oauth_config=oauth_config,
    )
    api_key = APIKeyStub(id=101, key="sk-ignored")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    token_requests: list[httpx.Request] = []
    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://auth.example.com/oauth/token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": "mock-access-token", "expires_in": 3600},
            )
        upstream_requests.append(request)
        return httpx.Response(200, json={"id": "oauth-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert len(token_requests) == 1
    assert len(upstream_requests) == 1
    sent_request = upstream_requests[0]
    assert sent_request.headers.get("authorization") == "Bearer mock-access-token"


@pytest.mark.asyncio
async def test_oauth_retries_on_401_with_force_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth_config = json.dumps({
        "token_url": "https://auth.example.com/oauth/token",
        "client_id": "retry-client",
        "client_secret": "retry-secret",
    })
    endpoint = EndpointStub(
        id=110,
        name="OAuthRetryEndpoint",
        base_url="https://api.example.com",
        oauth_config=oauth_config,
    )
    api_key = APIKeyStub(id=111, key="sk-retry-ignored")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    token_requests: list[httpx.Request] = []
    upstream_requests: list[httpx.Request] = []
    call_counter = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://auth.example.com/oauth/token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": "fresh-token", "expires_in": 3600},
            )
        upstream_requests.append(request)
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"id": "oauth-retry-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert len(upstream_requests) == 2
    assert len(token_requests) == 2
    assert upstream_requests[1].headers.get("authorization") == "Bearer fresh-token"


@pytest.mark.asyncio
async def test_oauth_uses_cached_token_on_subsequent_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth_config = json.dumps({
        "token_url": "https://auth.example.com/oauth/token",
        "client_id": "cache-client",
        "client_secret": "cache-secret",
    })
    endpoint = EndpointStub(
        id=120,
        name="OAuthCacheEndpoint",
        base_url="https://api.example.com",
        oauth_config=oauth_config,
    )
    api_key = APIKeyStub(id=121, key="sk-cache-ignored")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    token_requests: list[httpx.Request] = []
    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://auth.example.com/oauth/token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": "cached-token", "expires_in": 3600},
            )
        upstream_requests.append(request)
        return httpx.Response(200, json={"id": "oauth-cache-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response1 = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "first"},
        )
        assert response1.status_code == 200
        assert len(token_requests) == 1
        assert len(upstream_requests) == 1

        response2 = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "second"},
        )
        assert response2.status_code == 200

    await upstream_client.aclose()

    assert len(token_requests) == 1
    assert len(upstream_requests) == 2
    assert upstream_requests[0].headers.get("authorization") == "Bearer cached-token"
    assert upstream_requests[1].headers.get("authorization") == "Bearer cached-token"


@pytest.mark.asyncio
async def test_request_body_template_replaces_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证请求体模板能够正确替换 {{model}} 和 {{prompt}} 等变量"""
    template = json.dumps({
        "model": "{{model}}",
        "prompt": "{{prompt}}",
        "max_tokens": 1024,
        "custom_field": "{{custom_value}}",
    })
    endpoint = EndpointStub(
        id=1,
        name="TemplateEndpoint",
        base_url="https://api.example.com",
        request_body_template=template,
    )
    api_key = APIKeyStub(id=101, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o-mini")

    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"choices": [{"text": "ok"}]})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={
                "model": "gpt-4o-mini",
                "custom_value": "my-value",
                "messages": [{"role": "user", "content": "Hello world"}],
            },
        )
        assert response.status_code == 200

    await upstream_client.aclose()

    assert len(upstream_requests) == 1
    sent_body = json.loads(upstream_requests[0].content)
    assert sent_body["model"] == "gpt-4o-mini"
    assert sent_body["prompt"] == "Hello world"
    assert sent_body["max_tokens"] == 1024
    assert sent_body["custom_field"] == "my-value"


@pytest.mark.asyncio
async def test_request_body_template_fallback_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证模板渲染结果不是合法 JSON 时回退到原始请求体"""
    endpoint = EndpointStub(
        id=2,
        name="InvalidTemplateEndpoint",
        base_url="https://api.example.com",
        request_body_template="this is not json {{model}}",
    )
    api_key = APIKeyStub(id=102, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o-mini")

    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"choices": [{"text": "ok"}]})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "test"},
        )
        assert response.status_code == 200

    await upstream_client.aclose()

    assert len(upstream_requests) == 1
    sent_body = json.loads(upstream_requests[0].content)
    # 应该使用原始请求体，而不是模板
    assert sent_body["model"] == "gpt-4o-mini"
    assert sent_body["prompt"] == "test"

import json
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.base import Base
from app.db.models import FactoryAccessKey
from app.db.session import get_session
from app.services.agent_transport import AgentResponse, AgentUnavailableError
from app.services.router import RouteCandidate
from app.services.secrets import ENCRYPTED_SECRET_PREFIX, encrypt_oauth_config
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app
from conftest import TestMemoryRedis as MemoryRedis


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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

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
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["rule_group"] == "default"
    assert response.headers.get("x-request-id")
    assert response.headers.get("x-trace-id")
    assert "x-real-model" not in response.headers
    assert "x-api-key-id" not in response.headers
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
    await asyncio.sleep(0)

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
async def test_standard_provider_ignores_custom_endpoint_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=93,
        name="OpenAI",
        base_url="https://api.example.com",
        provider="openai",
        extra_headers=json.dumps({"X-Injected": "yes"}),
        extra_cookies="session=custom",
        extra_query_params=json.dumps({"api-version": "custom"}),
        url_path_suffix="/custom/path",
    )
    api_key = APIKeyStub(id=94, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "chatcmpl-openai"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions?existing=1",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/chat/completions"
    assert sent_request.url.query == b"existing=1"
    assert sent_request.headers.get("x-injected") is None
    assert sent_request.headers.get("cookie") is None


@pytest.mark.asyncio
async def test_custom_provider_applies_endpoint_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=95,
        name="Custom",
        base_url="https://custom.example.com",
        provider="custom",
        extra_headers=json.dumps({"X-Injected": "yes"}),
        extra_cookies="session=custom",
        extra_query_params=json.dumps({"api-version": "custom"}),
        url_path_suffix="/custom/path",
    )
    api_key = APIKeyStub(id=96, key="sk-custom")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="custom/model")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "custom-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions?existing=1",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/custom/path"
    assert sent_request.url.query == b"existing=1&api-version=custom"
    assert sent_request.headers.get("x-injected") == "yes"
    assert sent_request.headers.get("cookie") == "session=custom"


@pytest.mark.asyncio
async def test_standard_provider_returns_raw_non_stream_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=97, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=98, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    raw_response = b'{"id":"raw",   "usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=raw_response,
        )

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
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.content == raw_response
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.total_tokens == 3


@pytest.mark.asyncio
async def test_factory_access_key_cannot_escalate_rule_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()
    factory_key = FactoryAccessKey(name="limited", key="rk-limited", is_active=True)
    factory_key.rule_groups = ["allowed"]
    session.add(factory_key)
    await session.commit()

    endpoint = EndpointStub(id=1, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=2, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")
    recorded: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["upstream_body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-access",
                "choices": [{"message": {"content": "ok"}}],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(master_auth_token="admin")
    redis = MemoryRedis()

    async def fake_get_redis():
        return redis

    async def override_session():
        yield session

    async def fake_get_candidates(  # noqa: ANN001
        self,
        session,
        model_alias: str,
        rule_group: str,
        **kwargs,
    ):
        recorded["model_alias"] = model_alias
        recorded["rule_group"] = rule_group
        return [candidate], rule_group

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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer rk-limited"},
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
                "rule_group": "blocked",
            },
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert recorded["model_alias"] == "gpt-4o"
    assert recorded["rule_group"] == "allowed"
    assert recorded["upstream_body"]["rule_group"] == "blocked"
    assert recorded["metrics"].requested_rule_group == "blocked"
    assert recorded["metrics"].rule_group == "allowed"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_factory_access_key_cannot_escalate_rule_group_from_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()
    factory_key = FactoryAccessKey(name="limited", key="rk-header-limited", is_active=True)
    factory_key.rule_groups = ["allowed"]
    session.add(factory_key)
    await session.commit()

    endpoint = EndpointStub(id=11, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=12, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")
    recorded: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["upstream_body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-access-header",
                "choices": [{"message": {"content": "ok"}}],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(master_auth_token="admin")
    redis = MemoryRedis()

    async def fake_get_redis():
        return redis

    async def override_session():
        yield session

    async def fake_get_candidates(  # noqa: ANN001
        self,
        session,
        model_alias: str,
        rule_group: str,
        **kwargs,
    ):
        recorded["model_alias"] = model_alias
        recorded["rule_group"] = rule_group
        return [candidate], rule_group

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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={
                "Authorization": "Bearer rk-header-limited",
                "X-Rule-Group": "blocked",
            },
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert recorded["model_alias"] == "gpt-4o"
    assert recorded["rule_group"] == "allowed"
    assert "rule_group" not in recorded["upstream_body"]
    assert recorded["metrics"].requested_rule_group == "blocked"
    assert recorded["metrics"].rule_group == "allowed"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_openai_responses_passthrough_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=91, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=92, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4.1")

    upstream_payload = {"id": "resp-openai", "object": "response"}
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
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4.1-mini", "input": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4.1-mini"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/responses"
    assert sent_request.headers.get("authorization") == "Bearer sk-openai"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4.1"
    assert payload["input"] == "hi"


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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
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
async def test_gemini_passthrough_rewrites_model_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=31,
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="gemini",
        auth_header_name="x-goog-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=32, key="gemini-upstream-key")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gemini-1.5-pro",
    )

    upstream_payload = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {
            "promptTokenCount": 2,
            "candidatesTokenCount": 3,
            "totalTokenCount": 5,
        },
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {"contents": [{"parts": [{"text": "ping"}]}]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/gemini/v1beta/models/gemini-alias:generateContent",
            headers={"x-goog-api-key": "token", "X-Debug": "true"},
            json=body,
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert response.headers["x-real-model"] == "gemini-1.5-pro"
    assert response.headers["x-execution-mode"] == "direct"
    assert recorded["model_alias"] == "gemini-alias"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1beta/models/gemini-1.5-pro:generateContent"
    assert sent_request.headers.get("x-goog-api-key") == "gemini-upstream-key"
    assert json.loads(sent_request.content.decode("utf-8")) == body

    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.total_tokens == 5
    assert metrics.execution_mode == "direct"
    assert metrics.upstream_url.endswith("/v1beta/models/gemini-1.5-pro:generateContent")


@pytest.mark.asyncio
async def test_openai_chat_preserves_tools_and_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=42, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=43, key="sk-tools")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4.1")
    upstream_payload = {"id": "chat-tools", "choices": [{"message": {"content": "ok"}}]}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {
        "model": "gpt-alias",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "parallel_tool_calls": True,
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json=body,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    sent_payload = json.loads(requests[0].content)
    assert sent_payload["model"] == "gpt-4.1"
    assert sent_payload["tools"] == body["tools"]
    assert sent_payload["tool_choice"] == "auto"
    assert sent_payload["response_format"] == body["response_format"]
    assert sent_payload["parallel_tool_calls"] is True


@pytest.mark.asyncio
async def test_anthropic_messages_stream_preserves_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=44,
        name="Anthropic",
        base_url="https://api.anthropic.com",
        provider="anthropic",
        auth_header_name="x-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=45, key="sk-anthropic")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="claude-sonnet")
    requests: list[httpx.Request] = []
    stream_payload = b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\"}\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {
        "model": "claude-alias",
        "stream": True,
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image", "source": {"type": "base64", "data": "abc"}},
                ],
            }
        ],
        "thinking": {"type": "enabled", "budget_tokens": 1024},
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/anthropic/v1/messages",
            headers={"x-api-key": "token"},
            json=body,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.content == stream_payload
    sent_payload = json.loads(requests[0].content)
    assert sent_payload["model"] == "claude-sonnet"
    assert sent_payload["messages"] == body["messages"]
    assert sent_payload["thinking"] == body["thinking"]
    assert requests[0].headers.get("x-api-key") == "sk-anthropic"


@pytest.mark.asyncio
async def test_gemini_stream_generate_content_rewrites_model_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=46,
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="gemini",
        auth_header_name="x-goog-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=47, key="gemini-key")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gemini-2.0-flash",
    )
    requests: list[httpx.Request] = []
    stream_payload = b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/gemini/v1beta/models/gemini-alias:streamGenerateContent",
            headers={"x-goog-api-key": "token", "accept": "text/event-stream"},
            json=body,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.content == stream_payload
    assert requests[0].url.path == "/v1beta/models/gemini-2.0-flash:streamGenerateContent"
    assert json.loads(requests[0].content) == body


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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json=raw_payload,
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

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
    assert payload["rule_group"] == "qiniu"
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.requested_rule_group == "qiniu"
    assert metrics.rule_group == "qiniu"


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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
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
async def test_proxy_retries_primary_then_skips_open_circuit_on_next_request(
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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )
        second_response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi again"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-retry-ok"
    assert second_response.status_code == 200
    assert second_response.json()["id"] == "cmpl-retry-ok"
    assert len(requests) == 5
    primary_requests = [
        request for request in requests if request.headers.get("Authorization") == "Bearer sk-primary"
    ]
    fallback_requests = [
        request for request in requests if request.headers.get("Authorization") == "Bearer sk-fallback"
    ]
    assert len(primary_requests) == 3
    assert len(fallback_requests) == 2
    assert response.headers["x-api-key-id"] == str(fallback_key.id)
    assert second_response.headers["x-api-key-id"] == str(fallback_key.id)
    redis = recorded["redis"]
    assert redis.store[f"circuit:{primary_key.id}:state"] == "open"


@pytest.mark.asyncio
async def test_proxy_does_not_retry_same_candidate_on_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=14, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=15, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=16, key="sk-primary")
    fallback_key = APIKeyStub(id=17, key="sk-fallback")
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
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(200, json={"id": "cmpl-fallback-ok"})

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
    assert len(requests) == 2
    assert requests[0].headers.get("Authorization") == "Bearer sk-primary"
    assert requests[1].headers.get("Authorization") == "Bearer sk-fallback"


@pytest.mark.asyncio
async def test_proxy_falls_back_on_bad_request_without_opening_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=18, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=19, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=20, key="sk-primary")
    fallback_key = APIKeyStub(id=21, key="sk-fallback")
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
            return httpx.Response(400, json={"error": "model_not_supported"})
        return httpx.Response(200, json={"id": "cmpl-fallback-ok"})

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
    assert response.json()["id"] == "cmpl-fallback-ok"
    assert len(requests) == 2
    assert requests[0].headers.get("Authorization") == "Bearer sk-primary"
    assert requests[1].headers.get("Authorization") == "Bearer sk-fallback"
    redis = recorded["redis"]
    assert f"circuit:{primary_key.id}:state" not in redis.store


@pytest.mark.asyncio
async def test_proxy_falls_back_on_semantic_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=32, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=33, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=34, key="sk-primary")
    fallback_key = APIKeyStub(id=35, key="sk-fallback")
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
            return httpx.Response(
                200,
                json={"error": {"message": "insufficient quota"}},
            )
        return httpx.Response(200, json={"id": "cmpl-semantic-fallback-ok"})

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
    assert response.json()["id"] == "cmpl-semantic-fallback-ok"
    assert len(requests) == 2
    assert requests[0].headers.get("Authorization") == "Bearer sk-primary"
    assert requests[1].headers.get("Authorization") == "Bearer sk-fallback"
    redis = recorded["redis"]
    assert redis.store[f"circuit:{primary_key.id}:failures"] == "1"
    attempts = recorded.get("attempts")
    assert attempts is not None
    assert [attempt.outcome for attempt in attempts] == ["fallback", "success"]
    assert attempts[0].failure_reason == "semantic_error_field"
    assert attempts[1].api_key_id == fallback_key.id


@pytest.mark.asyncio
async def test_proxy_does_not_fallback_on_nullable_response_error_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=36, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=37, key="sk-primary")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    requests: list[httpx.Request] = []
    upstream_payload = {
        "id": "resp-ok",
        "object": "response",
        "status": "completed",
        "error": None,
        "output": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "input": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert len(requests) == 1


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
    oauth_config = json.dumps(
        encrypt_oauth_config(
            {
                "token_url": "https://auth.example.com/oauth/token",
                "client_id": "test-client",
                "client_secret": "test-secret",
            },
            settings=Settings(master_auth_token="token"),
        )
    )
    assert ENCRYPTED_SECRET_PREFIX in oauth_config
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
    token_body = token_requests[0].content.decode("utf-8")
    assert "client_secret=test-secret" in token_body
    assert ENCRYPTED_SECRET_PREFIX not in token_body
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
    """验证 custom provider 请求体模板能够正确替换变量"""
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
        provider="custom",
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
async def test_standard_provider_ignores_request_body_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = json.dumps({"model": "{{model}}", "prompt": "{{prompt}}"})
    endpoint = EndpointStub(
        id=3,
        name="StandardTemplateIgnored",
        base_url="https://api.example.com",
        provider="openai",
        request_body_template=template,
    )
    api_key = APIKeyStub(id=103, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

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
    assert sent_body["model"] == "gpt-4o"
    assert sent_body["custom_value"] == "my-value"
    assert "prompt" not in sent_body


@pytest.mark.asyncio
async def test_request_body_template_fallback_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证模板渲染结果不是合法 JSON 时回退到原始请求体"""
    endpoint = EndpointStub(
        id=2,
        name="InvalidTemplateEndpoint",
        base_url="https://api.example.com",
        provider="custom",
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

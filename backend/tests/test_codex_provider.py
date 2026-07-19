import json
import asyncio
import base64
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.api.v1.route_modules import proxy_direct_handler
from app.core.config import Settings
from app.db.base import Base
from app.db.models import APIKey, Endpoint
from app.db.session import get_session
from app.services.codex_oauth import (
    CodexCredential,
    normalize_codex_credential_json,
    parse_codex_credential,
    resolve_codex_credential,
)
from app.services import endpoint_transport
from app.services.agent_transport import AgentResponse
from app.services.router import RouteCandidate
from conftest import TestMemoryRedis as MemoryRedis
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


def _mock_codex_key(**overrides: object) -> str:
    payload = {
        "access_token": "mock-access-token",
        "refresh_token": "mock-refresh-token",
        "account_id": "mock-account-id",
        "expires_at": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _mock_jwt(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"mock.{encoded}.signature"


def test_codex_credential_uses_jwt_exp_when_expiry_field_is_missing() -> None:
    expires_at = int(time.time()) + 1800
    credential = parse_codex_credential(
        json.dumps(
            {
                "access_token": _mock_jwt(
                    {"exp": expires_at, "chatgpt_account_id": "jwt-account"}
                )
            }
        )
    )

    assert credential.expires_at == expires_at
    assert credential.account_id == "jwt-account"


def test_normalize_codex_credential_supports_nested_tokens_and_drops_extra_data() -> None:
    normalized = json.loads(
        normalize_codex_credential_json(
            json.dumps(
                {
                    "email": "private@example.test",
                    "id_token": "private-id-token",
                    "tokens": {
                        "access_token": "nested-access",
                        "refresh_token": "nested-refresh",
                        "account_id": "nested-account",
                        "expires_at": 1_900_000_000,
                    },
                }
            )
        )
    )

    assert normalized == {
        "access_token": "nested-access",
        "refresh_token": "nested-refresh",
        "account_id": "nested-account",
        "expires_at": 1_900_000_000,
    }


@pytest.mark.asyncio
async def test_codex_manual_probe_unions_models_from_every_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()
    endpoint = Endpoint(
        name="Codex Multi Auth",
        base_url="https://chatgpt.example.test",
        provider="codex",
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    session.add_all(
        [
            APIKey(
                endpoint_id=endpoint.id,
                key=_mock_codex_key(access_token="access-a", account_id="account-a"),
                is_active=True,
            ),
            APIKey(
                endpoint_id=endpoint.id,
                key=_mock_codex_key(access_token="access-b", account_id="account-b"),
                is_active=True,
            ),
        ]
    )
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer access-a":
            return httpx.Response(
                200, json={"models": [{"slug": "model-a"}, {"slug": "shared"}]}
            )
        return httpx.Response(
            200, json={"models": [{"slug": "model-b"}, {"slug": "shared"}]}
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    redis = MemoryRedis()

    async def override_session():
        yield session

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    async def override_redis():
        return redis

    monkeypatch.setattr(
        routes_module,
        "get_settings",
        lambda: Settings(
            master_auth_token="token", admin_legacy_master_bearer_enabled=True
        ),
    )
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)
    monkeypatch.setattr(routes_module, "get_redis", override_redis)
    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/endpoints/{endpoint.id}/probe",
            headers={"Authorization": "Bearer token"},
        )

    await upstream_client.aclose()
    assert response.status_code == 200
    assert response.json()["discovered_models"] == ["model-a", "shared", "model-b"]

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_codex_provider_uses_backend_api_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=201,
        name="Codex",
        base_url="https://chatgpt.example.test",
        provider="codex",
    )
    api_key = APIKeyStub(id=202, key=_mock_codex_key())
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gpt-5.5-codex-real",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "x-codex-primary-used-percent": "12.5",
                "x-codex-primary-window-minutes": "180",
                "x-codex-secondary-used-percent": "34.5",
                "x-codex-secondary-window-minutes": "10080",
            },
            json={"id": "resp-codex", "object": "response"},
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={
                "Authorization": "Bearer token",
                "User-Agent": "codex-cli/1.0",
                "Originator": "codex_cli_rs",
                "Session-Id": "sess-123",
                "Thread-Id": "thread-123",
            },
            json={
                "model": "gpt-5.5",
                "input": "hi",
                "max_output_tokens": 64,
                "temperature": 0.2,
                "stream": False,
            },
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert recorded["candidate_kwargs"]["exposure_format"] == "codex"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/backend-api/codex/responses"
    assert sent_request.headers.get("authorization") == "Bearer mock-access-token"
    assert sent_request.headers.get("chatgpt-account-id") == "mock-account-id"
    assert sent_request.headers.get("openai-beta") == "responses=experimental"
    assert sent_request.headers.get("originator") == "codex_cli_rs"
    assert sent_request.headers.get("session-id") == "sess-123"
    assert sent_request.headers.get_list("content-type") == ["application/json"]
    assert sent_request.headers.get_list("accept") == ["text/event-stream"]
    assert sent_request.headers.get_list("openai-beta") == ["responses=experimental"]
    body = json.loads(sent_request.content.decode("utf-8"))
    assert body["model"] == "gpt-5.5-codex-real"
    assert body["instructions"] == ""
    assert body["store"] is False
    assert body["stream"] is True
    assert "max_output_tokens" not in body
    assert "temperature" not in body
    raw_usage = await recorded["redis"].get("codex:usage:202")
    assert raw_usage
    usage = json.loads(raw_usage)
    assert usage["primary"]["used_percent"] == 12.5
    assert usage["secondary"]["window_minutes"] == 10080


@pytest.mark.asyncio
async def test_codex_provider_refreshes_and_retries_once_after_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=211,
        name="Codex",
        base_url="https://chatgpt.example.test",
        provider="codex",
    )
    api_key = APIKeyStub(id=212, key=_mock_codex_key())
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gpt-5.5-codex-real",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    async def fake_resolve(*args, **kwargs) -> CodexCredential:  # noqa: ANN002, ANN003
        assert kwargs["force_refresh"] is True
        return CodexCredential(
            access_token="refreshed-access-token",
            account_id="mock-account-id",
        )

    monkeypatch.setattr(proxy_direct_handler, "resolve_codex_credential", fake_resolve)
    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token", "User-Agent": "codex-cli/1.0"},
            json={"model": "gpt-5.5", "input": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert len(requests) == 2
    assert requests[0].headers["authorization"] == "Bearer mock-access-token"
    assert requests[1].headers["authorization"] == "Bearer refreshed-access-token"


@pytest.mark.asyncio
async def test_codex_model_error_falls_back_to_next_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=221,
        name="Codex",
        base_url="https://chatgpt.example.test",
        provider="codex",
    )
    first_key = APIKeyStub(
        id=222,
        key=_mock_codex_key(access_token="access-a", account_id="account-a"),
    )
    second_key = APIKeyStub(
        id=223,
        key=_mock_codex_key(access_token="access-b", account_id="account-b"),
    )
    candidates = [
        RouteCandidate(
            api_key=first_key,
            endpoint=endpoint,
            real_model="gpt-5.6-sol",
        ),
        RouteCandidate(
            api_key=second_key,
            endpoint=endpoint,
            real_model="gpt-5.6-sol",
        ),
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["authorization"] == "Bearer access-a":
            return httpx.Response(400, json={"error": "model_not_supported"})
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidates, upstream_client, recorded)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token", "User-Agent": "codex-cli/1.0"},
            json={"model": "gpt-5.6-sol", "input": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer access-a",
        "Bearer access-b",
    ]
    assert [attempt.outcome for attempt in recorded["attempts"]] == [
        "fallback",
        "success",
    ]


@pytest.mark.asyncio
async def test_codex_credential_refresh_uses_mock_token_endpoint() -> None:
    api_key = APIKeyStub(
        id=203,
        key=_mock_codex_key(access_token="", expires_at=int(time.time()) - 60),
    )

    class FakeSession:
        committed = False
        rolled_back = False

        async def commit(self) -> None:
            self.committed = True

        async def rollback(self) -> None:
            self.rolled_back = True

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "mock-new-access-token",
                "refresh_token": "mock-new-refresh-token",
                "account_id": "mock-new-account-id",
                "expires_in": 7200,
            },
        )

    settings = Settings(codex_oauth_token_url="https://auth.example.test/oauth/token")
    session = FakeSession()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    credential = await resolve_codex_credential(
        api_key,
        client=client,
        session=session,  # type: ignore[arg-type]
        settings=settings,
    )
    await client.aclose()

    assert credential.access_token == "mock-new-access-token"
    assert credential.refresh_token == "mock-new-refresh-token"
    assert credential.account_id == "mock-new-account-id"
    assert session.committed is True
    assert requests
    assert requests[0].url == "https://auth.example.test/oauth/token"
    persisted = parse_codex_credential(api_key.key)
    assert persisted.access_token == "mock-new-access-token"


@pytest.mark.asyncio
async def test_codex_credential_refresh_uses_endpoint_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = APIKeyStub(
        id=206,
        key=_mock_codex_key(access_token="", expires_at=int(time.time()) - 60),
    )
    endpoint = EndpointStub(
        id=207,
        name="Codex Agent",
        base_url="https://chatgpt.example.test",
        provider="codex",
        agent_node="edge-codex",
    )

    class FakeSession:
        async def commit(self) -> None:
            return None

    class FakeAgentManager:
        def __init__(self) -> None:
            self.calls = []

        async def send_request(self, agent_name, request):  # noqa: ANN001
            self.calls.append((agent_name, request))
            return AgentResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=json.dumps(
                    {
                        "access_token": "agent-access-token",
                        "refresh_token": "agent-refresh-token",
                        "account_id": "agent-account-id",
                        "expires_in": 3600,
                    }
                ).encode("utf-8"),
            )

    manager = FakeAgentManager()
    monkeypatch.setattr(endpoint_transport, "get_agent_manager", lambda: manager)

    def direct_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Codex refresh for via_agent must not connect directly")

    client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))
    credential = await resolve_codex_credential(
        api_key,
        client=client,
        session=FakeSession(),  # type: ignore[arg-type]
        settings=Settings(codex_oauth_token_url="https://auth.example.test/oauth/token"),
        endpoint=endpoint,
    )
    await client.aclose()

    assert credential.access_token == "agent-access-token"
    assert len(manager.calls) == 1
    agent_name, request = manager.calls[0]
    assert agent_name == "edge-codex"
    assert request.url == "https://auth.example.test/oauth/token"
    form = parse_qs(request.body.decode("utf-8"))
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["mock-refresh-token"]


@pytest.mark.asyncio
async def test_codex_credential_refreshes_when_account_id_missing() -> None:
    api_key = APIKeyStub(
        id=204,
        key=_mock_codex_key(account_id=""),
    )

    class FakeSession:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "mock-refreshed-access-token",
                "refresh_token": "mock-refreshed-refresh-token",
                "account_id": "mock-refreshed-account-id",
                "expires_in": 3600,
            },
        )

    settings = Settings(codex_oauth_token_url="https://auth.example.test/oauth/token")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    credential = await resolve_codex_credential(
        api_key,
        client=client,
        session=FakeSession(),  # type: ignore[arg-type]
        settings=settings,
    )
    await client.aclose()

    assert credential.access_token == "mock-refreshed-access-token"
    assert credential.account_id == "mock-refreshed-account-id"


@pytest.mark.asyncio
async def test_concurrent_codex_refresh_only_calls_token_endpoint_once() -> None:
    api_key = APIKeyStub(
        id=205,
        key=_mock_codex_key(access_token="", expires_at=int(time.time()) - 60),
    )

    class FakeSession:
        async def commit(self) -> None:
            return None

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "shared-access-token",
                "refresh_token": "shared-refresh-token",
                "account_id": "shared-account",
                "expires_in": 3600,
            },
        )

    redis = MemoryRedis()
    settings = Settings(codex_oauth_token_url="https://auth.example.test/oauth/token")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    first, second = await asyncio.gather(
        resolve_codex_credential(
            api_key,
            client=client,
            session=FakeSession(),  # type: ignore[arg-type]
            redis=redis,
            settings=settings,
        ),
        resolve_codex_credential(
            api_key,
            client=client,
            session=FakeSession(),  # type: ignore[arg-type]
            redis=redis,
            settings=settings,
        ),
    )
    await client.aclose()

    assert first.access_token == "shared-access-token"
    assert second.access_token == "shared-access-token"
    assert len(requests) == 1

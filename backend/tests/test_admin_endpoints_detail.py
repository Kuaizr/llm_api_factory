import httpx
from datetime import datetime, timezone
import json

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.core.redis import MemoryRedis
from app.db.base import Base
from app.db.models import APIKey, Endpoint, ModelMap, RequestAttemptLog, RequestLog
from app.db.session import get_session
from app.services.agent_transport import AgentResponse
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import HealthProbeResult, HealthProbeStore
from app.services.secrets import ENCRYPTED_SECRET_PREFIX, decrypt_secret_value


@pytest.mark.asyncio
async def test_admin_endpoints_detail_includes_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        provider="openai",
        strategy="weighted_round_robin",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(
        endpoint_id=endpoint.id,
        key="sk-test",
        name="Main",
        rule_group="default",
        is_active=True,
        daily_limit=100,
        rpm_limit=10,
        used_today=5,
    )
    session.add(api_key)
    session.add(
        ModelMap(
            endpoint_id=endpoint.id,
            model_alias="gpt-4",
            real_model="gpt-4",
        )
    )
    await session.commit()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    redis = MemoryRedis()
    probe_store = HealthProbeStore(redis)
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    await probe_store.write(
        HealthProbeResult(
            api_key_id=api_key.id,
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
            real_model=None,
            status="success",
            status_code=200,
            latency_ms=120,
            checked_at=now,
        )
    )
    await probe_store.write(
        HealthProbeResult(
            api_key_id=api_key.id,
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
            real_model=None,
            status="failure",
            status_code=503,
            latency_ms=200,
            checked_at=now,
        )
    )

    async def override_redis():
        return redis

    monkeypatch.setattr(routes_module, "get_redis", override_redis)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/endpoints", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["model_count"] == 1
    assert payload[0]["keys"][0]["name"] == "Main"
    assert payload[0]["keys"][0]["rule_group"] == "default"
    assert payload[0]["is_active"] is True
    assert payload[0]["latency"] == 120
    assert payload[0]["uptime"] == 50.0

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_endpoint_accepts_disable_probe_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/endpoints",
            headers={"Authorization": "Bearer token"},
            json={
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "provider": "openai",
                "probe_interval_seconds": -1,
                "is_active": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["probe_interval_seconds"] == -1

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_standard_endpoint_clears_custom_only_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/endpoints",
            headers={"Authorization": "Bearer token"},
            json={
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "provider": "openai",
                "url_path_suffix": "/custom",
                "extra_headers": {"X-Custom": "yes"},
                "extra_cookies": "session=custom",
                "extra_query_params": {"api-version": "custom"},
                "oauth_config": {
                    "token_url": "https://auth.example.com/oauth/token",
                    "client_id": "client",
                    "client_secret": "secret",
                },
                "request_body_template": json.dumps({"model": "{{model}}"}),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["url_path_suffix"] is None
    assert payload["extra_headers"] is None
    assert payload["extra_cookies"] is None
    assert payload["extra_query_params"] is None
    assert payload["oauth_config"] is None
    assert payload["request_body_template"] is None

    endpoint = await session.get(Endpoint, payload["id"])
    assert endpoint is not None
    assert endpoint.url_path_suffix is None
    assert endpoint.extra_headers is None
    assert endpoint.extra_cookies is None
    assert endpoint.extra_query_params is None
    assert endpoint.oauth_config is None
    assert endpoint.request_body_template is None

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_endpoint_update_clears_agent_when_agent_node_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()
    endpoint = Endpoint(
        name="ViaAgent",
        base_url="https://api.example.com/v1",
        provider="openai",
        access_mode="via_agent",
        agent_node="edge-vps",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/admin/endpoints/{endpoint.id}",
            headers={"Authorization": "Bearer token"},
            json={"agent_node": ""},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_mode"] == "direct"
    assert payload["agent_node"] is None

    await session.refresh(endpoint)
    assert endpoint.access_mode == "direct"
    assert endpoint.agent_node is None

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_probe_records_success_status(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(name="OpenAI", base_url="https://api.openai.com", is_active=True)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-probe", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=90,
    )
    redis = MemoryRedis()
    probe_store = HealthProbeStore(redis)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_redis():
        return redis

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", override_redis)
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/endpoints/{endpoint.id}/probe",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["probe_status"] == "success"
    assert payload["probe_status_code"] == 200
    assert payload["discovered_models"] == ["gpt-4o-mini"]
    assert len(payload["manual_models"]) == 1
    assert payload["manual_models"][0]["model_alias"] == "gpt-4o-mini"
    assert payload["manual_models"][0]["real_model"] == "gpt-4o-mini"
    assert payload["manual_models"][0]["probe_managed"] is True

    synced_models = (await session.execute(select(ModelMap).where(ModelMap.endpoint_id == endpoint.id))).scalars().all()
    assert len(synced_models) == 1
    assert synced_models[0].probe_managed is True

    probe = await probe_store.read(api_key.id)
    assert probe is not None
    assert probe.status == "success"
    assert probe.status_code == 200

    breaker = CircuitBreaker(redis, settings=settings)
    breaker_status = await breaker.get_status(api_key.id)
    assert breaker_status.state == "closed"

    await upstream_client.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_probe_sync_overwrites_auto_keeps_manual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(name="OpenAI", base_url="https://api.openai.com", is_active=True)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-probe", is_active=True)
    session.add(api_key)
    session.add_all(
        [
            ModelMap(
                endpoint_id=endpoint.id,
                model_alias="manual-kept",
                real_model="gpt-4o-mini",
                probe_managed=False,
            ),
            ModelMap(
                endpoint_id=endpoint.id,
                model_alias="old-auto",
                real_model="gpt-3.5-old",
                probe_managed=True,
            ),
            ModelMap(
                endpoint_id=endpoint.id,
                model_alias="stale-alias",
                real_model="gpt-4.1-mini",
                probe_managed=True,
            ),
        ]
    )
    await session.commit()
    await session.refresh(api_key)

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=90,
    )
    redis = MemoryRedis()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4.1-mini"}, {"id": "gpt-4o-mini"}]},
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_redis():
        return redis

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", override_redis)
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/endpoints/{endpoint.id}/probe",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["discovered_models"] == ["gpt-4.1-mini", "gpt-4o-mini"]
    assert len(payload["manual_models"]) == 2

    synced_models = (
        await session.execute(
            select(ModelMap).where(ModelMap.endpoint_id == endpoint.id).order_by(ModelMap.id)
        )
    ).scalars().all()
    assert len(synced_models) == 2

    manual_model = next(model for model in synced_models if model.real_model == "gpt-4o-mini")
    assert manual_model.model_alias == "manual-kept"
    assert manual_model.probe_managed is False

    auto_model = next(model for model in synced_models if model.real_model == "gpt-4.1-mini")
    assert auto_model.model_alias == "gpt-4.1-mini"
    assert auto_model.probe_managed is True

    assert all(model.real_model != "gpt-3.5-old" for model in synced_models)

    await upstream_client.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_probe_records_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(name="OpenAI", base_url="https://api.openai.com", is_active=True)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-probe", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=90,
    )
    redis = MemoryRedis()
    probe_store = HealthProbeStore(redis)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "upstream down"}})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_redis():
        return redis

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", override_redis)
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/endpoints/{endpoint.id}/probe",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["probe_status"] == "failure"
    assert payload["probe_status_code"] == 503
    assert payload["discovered_models"] == []
    assert "HTTP 503" in (payload["probe_message"] or "")

    probe = await probe_store.read(api_key.id)
    assert probe is not None
    assert probe.status == "failure"
    assert probe.status_code == 503

    breaker = CircuitBreaker(redis, settings=settings)
    breaker_status = await breaker.get_status(api_key.id)
    assert breaker_status.state == "open"

    await upstream_client.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_probe_records_request_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(name="OpenAI", base_url="https://api.openai.com", is_active=True)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-probe", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=90,
    )
    redis = MemoryRedis()
    probe_store = HealthProbeStore(redis)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("probe connection failed", request=request)

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_redis():
        return redis

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", override_redis)
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    with caplog.at_level("ERROR"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/admin/endpoints/{endpoint.id}/probe",
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["probe_status"] == "error"
    assert payload["probe_status_code"] is None
    assert payload["discovered_models"] == []
    assert "探测请求执行失败" in (payload["probe_message"] or "")
    assert "endpoint_probe_failed" in caplog.text

    probe = await probe_store.read(api_key.id)
    assert probe is not None
    assert probe.status == "error"
    assert probe.status_code is None

    breaker = CircuitBreaker(redis, settings=settings)
    breaker_status = await breaker.get_status(api_key.id)
    assert breaker_status.state == "open"

    await upstream_client.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_probe_anthropic_uses_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        provider="anthropic",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-anthropic", is_active=True)
    session.add(api_key)
    session.add(
        ModelMap(
            endpoint_id=endpoint.id,
            model_alias="claude-3-5-haiku-latest",
            real_model="claude-3-5-haiku-latest",
        )
    )
    await session.commit()
    await session.refresh(api_key)

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=90,
    )
    redis = MemoryRedis()
    probe_store = HealthProbeStore(redis)
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"id": "msg_1", "type": "message"})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_redis():
        return redis

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", override_redis)
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/endpoints/{endpoint.id}/probe",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["probe_status"] == "success"
    assert response_payload["probe_status_code"] == 200
    assert response_payload["discovered_models"] == []
    assert len(response_payload["manual_models"]) == 1
    assert response_payload["manual_models"][0]["model_alias"] == "claude-3-5-haiku-latest"
    assert response_payload["manual_models"][0]["probe_managed"] is False
    assert response_payload["probe_message"] is None

    assert captured_requests
    sent_request = captured_requests[0]
    assert sent_request.method == "POST"
    assert sent_request.url.path == "/v1/messages"
    sent_payload = sent_request.read().decode("utf-8")
    assert "claude-3-5-haiku-latest" in sent_payload

    probe = await probe_store.read(api_key.id)
    assert probe is not None
    assert probe.status == "success"
    assert probe.status_code == 200

    await upstream_client.aclose()
    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_api_key_direct_test_uses_selected_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="DirectOpenAI",
        base_url="https://api.example.com/v1",
        provider="openai",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-direct", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "gpt-direct"
        assert body["messages"][0]["content"] == "你是什么模型"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "choices": [{"message": {"content": "我是 gpt-direct"}}],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True))
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/api-keys/{api_key.id}/test",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-direct"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status_code"] == 200
    assert payload["request_template"] == "chat"
    assert payload["output_text"] == "我是 gpt-direct"
    assert payload["upstream_url"] == "https://api.example.com/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer sk-direct"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_api_key_test_uses_agent_for_agent_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="AgentOpenAI",
        base_url="https://api.example.com/v1",
        provider="openai",
        access_mode="via_agent",
        agent_node="edge-vps",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-agent", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    class FakeAgentManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        async def send_request(self, agent_name: str, request):  # noqa: ANN001
            self.calls.append((agent_name, request))
            body = json.loads(request.body.decode("utf-8"))
            assert body["model"] == "gpt-agent"
            assert body["messages"][0]["content"] == "你是什么模型"
            return AgentResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=json.dumps(
                    {
                        "id": "chatcmpl-agent-test",
                        "choices": [{"message": {"content": "我是 agent"}}],
                    }
                ).encode("utf-8"),
            )

    agent_manager = FakeAgentManager()

    def direct_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("via_agent API key test must not call direct upstream")

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))

    async def override_session():
        yield session

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(
        routes_module,
        "get_settings",
        lambda: Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True),
    )
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)
    monkeypatch.setattr(routes_module, "get_agent_manager", lambda: agent_manager)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/api-keys/{api_key.id}/test",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-agent", "request_template": "chat"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["output_text"] == "我是 agent"
    assert len(agent_manager.calls) == 1
    agent_name, agent_request = agent_manager.calls[0]
    assert agent_name == "edge-vps"
    assert agent_request.url == "https://api.example.com/v1/chat/completions"
    assert agent_request.headers["Authorization"] == "Bearer sk-agent"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_api_key_create_encrypts_storage_and_direct_test_decrypts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="EncryptedKeyEndpoint",
        base_url="https://api.example.com/v1",
        provider="openai",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-encrypted",
                "choices": [{"message": {"content": "ok"}}],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    settings = Settings(
        master_auth_token="token",
        admin_legacy_master_bearer_enabled=True,
    )
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            f"/admin/endpoints/{endpoint.id}/keys",
            headers={"Authorization": "Bearer token"},
            json={"key": "sk-encrypted", "name": "Encrypted"},
        )
        assert create_response.status_code == 200
        key_id = create_response.json()["id"]
        assert create_response.json()["key"] == "sk-...pted"

        test_response = await client.post(
            f"/admin/api-keys/{key_id}/test",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-encrypted"},
        )

    await upstream_client.aclose()

    stored_key = await session.get(APIKey, key_id)
    assert stored_key is not None
    assert stored_key.key.startswith(ENCRYPTED_SECRET_PREFIX)
    assert decrypt_secret_value(stored_key.key, settings=settings) == "sk-encrypted"
    assert test_response.status_code == 200
    assert requests[0].headers["authorization"] == "Bearer sk-encrypted"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_endpoint_oauth_secret_is_encrypted_and_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post(
            "/admin/endpoints",
            headers={"Authorization": "Bearer token"},
            json={
                "name": "OAuthEndpoint",
                "base_url": "https://api.example.com/v1",
                "provider": "custom",
                "oauth_config": {
                    "token_url": "https://auth.example.com/oauth/token",
                    "client_id": "client",
                    "client_secret": "oauth-secret",
                },
            },
        )

        assert create_response.status_code == 200
        create_payload = create_response.json()
        endpoint_id = create_payload["id"]
        assert create_payload["oauth_config"] == {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "client",
            "client_secret": "********",
        }
        assert "oauth-secret" not in str(create_payload)

        stored_endpoint = await session.get(Endpoint, endpoint_id)
        assert stored_endpoint is not None
        stored_oauth = json.loads(stored_endpoint.oauth_config or "{}")
        original_encrypted_secret = stored_oauth["client_secret"]
        assert original_encrypted_secret.startswith(ENCRYPTED_SECRET_PREFIX)
        assert decrypt_secret_value(original_encrypted_secret, settings=settings) == "oauth-secret"

        list_response = await client.get(
            "/admin/endpoints",
            headers={"Authorization": "Bearer token"},
        )
        assert list_response.status_code == 200
        listed = next(item for item in list_response.json() if item["id"] == endpoint_id)
        assert listed["oauth_config"]["client_secret"] == "********"
        assert "oauth-secret" not in str(listed)

        update_response = await client.patch(
            f"/admin/endpoints/{endpoint_id}",
            headers={"Authorization": "Bearer token"},
            json={
                "oauth_config": {
                    "token_url": "https://auth.example.com/oauth/token",
                    "client_id": "client-updated",
                    "client_secret": "********",
                },
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["oauth_config"] == {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "client-updated",
            "client_secret": "********",
        }

    await session.refresh(stored_endpoint)
    updated_oauth = json.loads(stored_endpoint.oauth_config or "{}")
    assert updated_oauth["client_id"] == "client-updated"
    assert updated_oauth["client_secret"] == original_encrypted_secret
    assert decrypt_secret_value(updated_oauth["client_secret"], settings=settings) == "oauth-secret"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_api_key_delete_removes_related_request_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="DeleteKeyEndpoint",
        base_url="https://api.example.com/v1",
        provider="openai",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-delete", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    session.add(
        RequestLog(
            request_id="req-delete",
            trace_id="trace-delete",
            model_alias="gpt-delete",
            endpoint_id=endpoint.id,
            api_key_id=api_key.id,
            rule_group="default",
            latency_ms=10,
            status_code=200,
        )
    )
    session.add(
        RequestAttemptLog(
            request_id="req-delete",
            trace_id="trace-delete",
            model_alias="gpt-delete",
            endpoint_id=endpoint.id,
            api_key_id=api_key.id,
            rule_group="default",
            attempt_order=1,
            outcome="success",
            latency_ms=10,
        )
    )
    await session.commit()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(
            f"/admin/api-keys/{api_key.id}",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    assert (await session.execute(select(RequestLog))).scalars().all() == []
    assert (await session.execute(select(RequestAttemptLog))).scalars().all() == []

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_template", "expected_path", "expected_body_key", "response_payload"),
    [
        (
            "response",
            "/v1/responses",
            "input",
            {
                "id": "resp-test",
                "output": [{"content": [{"type": "output_text", "text": "我是 response"}]}],
            },
        ),
        (
            "codex",
            "/v1/responses",
            "input",
            {
                "id": "resp-codex-test",
                "output": [{"content": [{"type": "output_text", "text": "我是 codex"}]}],
            },
        ),
        (
            "claude",
            "/v1/messages",
            "messages",
            {"id": "msg-test", "content": [{"type": "text", "text": "我是 claude"}]},
        ),
        (
            "gemini",
            "/v1beta/models/gemini-test:generateContent",
            "contents",
            {
                "candidates": [
                    {"content": {"parts": [{"text": "我是 gemini"}]}}
                ]
            },
        ),
    ],
)
async def test_api_key_direct_test_supports_request_templates(
    request_template: str,
    expected_path: str,
    expected_body_key: str,
    response_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="TemplateEndpoint",
        base_url="https://api.example.com/v1",
        provider="openai",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-template", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.path == expected_path
        if request_template == "gemini":
            assert "model" not in body
        elif request_template == "codex":
            assert body["model"] == "gemini-test"
            assert body["store"] is False
            assert body["stream"] is True
            assert body["tool_choice"] == "auto"
            assert body["parallel_tool_calls"] is False
            assert str(body["prompt_cache_key"]).startswith("lmf-codex-test-")
            assert isinstance(body["client_metadata"], dict)
            assert body["client_metadata"]["session_id"].startswith(
                "lmf-codex-test-session-"
            )
            assert body["client_metadata"]["thread_id"].startswith(
                "lmf-codex-test-thread-"
            )
            assert request.headers.get("originator") == "codex_cli_rs"
            assert str(request.headers.get("session-id")).startswith(
                "lmf-codex-test-session-"
            )
            assert str(request.headers.get("thread-id")).startswith(
                "lmf-codex-test-thread-"
            )
            assert str(request.headers.get("x-client-request-id")).startswith(
                "lmf-codex-test-thread-"
            )
            assert str(request.headers.get("x-codex-installation-id")).startswith(
                "lmf-codex-test-installation-"
            )
            assert request.headers.get("x-codex-beta-features") == "responses"
            assert request.headers.get("x-codex-turn-metadata")
        else:
            assert body["model"] == "gemini-test"
        assert expected_body_key in body
        if request_template == "codex":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    'event: response.output_text.delta\n'
                    'data: {"type":"response.output_text.delta","delta":"我是 codex"}\n\n'
                    'data: [DONE]\n\n'
                ),
            )
        return httpx.Response(200, json=response_payload)

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_session():
        yield session

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_settings", lambda: Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True))
    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/admin/api-keys/{api_key.id}/test",
            headers={"Authorization": "Bearer token"},
            json={"model": "gemini-test", "request_template": request_template},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["request_template"] == request_template
    assert payload["upstream_url"] == f"https://api.example.com{expected_path}"
    assert payload["output_text"]

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_request_attempt_logs_endpoint_filters_by_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(name="OpenAI", base_url="https://api.example.com", is_active=True)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-attempt", is_active=True)
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    session.add_all(
        [
            RequestAttemptLog(
                request_id="req-1",
                trace_id="trace-1",
                model_alias="gpt-5",
                endpoint_id=endpoint.id,
                api_key_id=api_key.id,
                requested_rule_group="codex",
                rule_group="gpt-5.5",
                attempt_order=1,
                status_code=503,
                outcome="fallback",
                failure_reason="http_503",
                latency_ms=120,
                execution_mode="direct",
                upstream_url="https://api.example.com/v1/chat/completions",
            ),
            RequestAttemptLog(
                request_id="req-2",
                trace_id="trace-2",
                model_alias="gpt-5",
                endpoint_id=endpoint.id,
                api_key_id=api_key.id,
                requested_rule_group="codex",
                rule_group="gpt-5.5",
                attempt_order=1,
                status_code=200,
                outcome="success",
                failure_reason=None,
                latency_ms=80,
                execution_mode="direct",
                upstream_url="https://api.example.com/v1/chat/completions",
            ),
        ]
    )
    await session.commit()

    async def override_session():
        yield session

    monkeypatch.setattr(routes_module, "get_settings", lambda: Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True))

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/request-attempt-logs?request_id=req-1",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["request_id"] == "req-1"
    assert payload[0]["outcome"] == "fallback"
    assert payload[0]["failure_reason"] == "http_503"

    await session.close()
    await engine.dispose()

import httpx
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.core.redis import MemoryRedis
from app.db.base import Base
from app.db.models import APIKey, Endpoint, ModelMap
from app.db.session import get_session
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import HealthProbeResult, HealthProbeStore


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

    settings = Settings(master_auth_token="token")
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
    assert payload[0]["is_active"] is True
    assert payload[0]["latency"] == 120
    assert payload[0]["uptime"] == 50.0

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
        master_auth_token="token",
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
    assert payload
    assert payload[0]["model_alias"] == "gpt-4o-mini"

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
        master_auth_token="token",
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

    assert response.status_code == 502

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

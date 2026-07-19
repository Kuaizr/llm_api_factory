import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.base import Base
from app.db.models import APIKey, Endpoint, FactoryAccessKey, ModelMap, RoutingRule
from app.db.session import get_session
from app.services.access_keys import access_key_preview, hash_access_key
from app.services.router import RouteCandidate
from conftest import TestMemoryRedis as MemoryRedis
from proxy_test_utils import APIKeyStub, EndpointStub


@pytest.mark.asyncio
async def test_factory_access_key_cannot_escalate_rule_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()
    factory_key = FactoryAccessKey(
        name="limited",
        key=hash_access_key("rk-limited"),
        key_preview=access_key_preview("rk-limited"),
        is_active=True,
    )
    factory_key.rule_groups = ["allowed"]
    session.add(factory_key)
    await session.commit()

    endpoint = EndpointStub(id=1, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=2, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")
    recorded: dict[str, object] = {}

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
    upstream_body = recorded["upstream_body"]
    assert isinstance(upstream_body, dict)
    assert upstream_body["rule_group"] == "blocked"
    metrics = recorded["metrics"]
    assert metrics.requested_rule_group == "blocked"
    assert metrics.rule_group == "allowed"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_factory_access_key_does_not_fallback_to_default_when_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="DefaultOnly",
        base_url="https://api.example.com",
        provider="openai",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-default", is_active=True)
    api_key.assign_rule_groups(["default"])
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    session.add(
        ModelMap(
            endpoint_id=endpoint.id,
            model_alias="minimax/minimax-m3",
            real_model="minimax/minimax-m3",
        )
    )
    session.add(
        RoutingRule(
            model_pattern=".*",
            group_name="default",
            priority=0,
            is_active=True,
            target_key_ids_json=json.dumps(
                {"target_key_ids": [api_key.id], "strategy": "weighted_round_robin"}
            ),
        )
    )
    session.add(
        RoutingRule(
            model_pattern="minimax-m3",
            group_name="minimax",
            priority=10,
            is_active=True,
            target_key_ids_json=json.dumps(
                {"target_key_ids": [api_key.id], "strategy": "sequential"}
            ),
        )
    )
    factory_key = FactoryAccessKey(
        name="minimax-only",
        key=hash_access_key("fk-minimax-only"),
        key_preview=access_key_preview("fk-minimax-only"),
        is_active=True,
    )
    factory_key.rule_groups = ["minimax"]
    session.add(factory_key)
    await session.commit()

    settings = Settings(master_auth_token="admin")
    redis = MemoryRedis()

    async def fake_get_redis():
        return redis

    async def override_session():
        yield session

    async def fake_get_http_client() -> httpx.AsyncClient:
        raise AssertionError("default fallback must not call upstream")

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(routes_module, "get_notifier", lambda: None)
    monkeypatch.setattr(routes_module, "get_http_client", fake_get_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer fk-minimax-only"},
            json={
                "model": "minimax/minimax-m3",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No available API keys"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_factory_access_key_rejects_api_entry_not_enabled_for_rule_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    endpoint = Endpoint(
        name="Codex Group Key",
        base_url="https://api.example.com",
        provider="openai",
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    api_key = APIKey(endpoint_id=endpoint.id, key="sk-codex-group", is_active=True)
    api_key.assign_rule_groups(["codex-only"])
    session.add(api_key)
    await session.flush()
    session.add(
        ModelMap(
            endpoint_id=endpoint.id,
            model_alias="gpt-5.6-sol",
            real_model="gpt-5.6-sol",
        )
    )
    session.add(
        RoutingRule(
            model_pattern=".*",
            group_name="codex-only",
            priority=10,
            is_active=True,
            target_key_ids_json=json.dumps(
                {
                    "target_key_ids": [api_key.id],
                    "strategy": "sequential",
                    "exposure_formats": ["codex"],
                }
            ),
        )
    )
    factory_key = FactoryAccessKey(
        name="codex-entry-only",
        key=hash_access_key("fk-codex-entry-only"),
        key_preview=access_key_preview("fk-codex-entry-only"),
        is_active=True,
    )
    factory_key.rule_groups = ["codex-only"]
    session.add(factory_key)
    await session.commit()

    settings = Settings(master_auth_token="admin")
    redis = MemoryRedis()

    async def fake_get_redis():
        return redis

    async def override_session():
        yield session

    async def fake_get_http_client() -> httpx.AsyncClient:
        raise AssertionError("disabled Chat entry must not call upstream")

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(routes_module, "get_notifier", lambda: None)
    monkeypatch.setattr(routes_module, "get_http_client", fake_get_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer fk-codex-entry-only"},
            json={
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No available API keys"

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
    factory_key = FactoryAccessKey(
        name="limited",
        key=hash_access_key("rk-header-limited"),
        key_preview=access_key_preview("rk-header-limited"),
        is_active=True,
    )
    factory_key.rule_groups = ["allowed"]
    session.add(factory_key)
    await session.commit()

    endpoint = EndpointStub(id=11, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=12, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")
    recorded: dict[str, object] = {}

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
    upstream_body = recorded["upstream_body"]
    assert isinstance(upstream_body, dict)
    assert "rule_group" not in upstream_body
    metrics = recorded["metrics"]
    assert metrics.requested_rule_group == "blocked"
    assert metrics.rule_group == "allowed"

    await session.close()
    await engine.dispose()

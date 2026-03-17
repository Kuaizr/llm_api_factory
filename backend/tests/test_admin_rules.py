import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.base import Base
from app.db.models import APIKey, Endpoint, RequestLog
from app.db.session import get_session


@pytest.mark.asyncio
async def test_admin_rules_create_and_list(monkeypatch: pytest.MonkeyPatch) -> None:
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
        rule_group="alpha",
        is_active=True,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4.*",
                "group_name": "alpha",
                "priority": 10,
                "is_active": True,
                "target_key_ids": [api_key.id],
                "dump_enabled": True,
                "dump_path": "/tmp/alpha-dump",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["model_pattern"] == "gpt-4.*"
        assert payload["target_key_ids"] == [api_key.id]
        assert payload["dump_enabled"] is True
        assert payload["dump_path"] == "/tmp/alpha-dump"
        assert payload["request_count"] == 0

        session.add_all(
            [
                RequestLog(
                    request_id="req-1",
                    trace_id="trace-1",
                    model_alias="gpt-4.1",
                    endpoint_id=endpoint.id,
                    api_key_id=api_key.id,
                    prompt_tokens=10,
                    completion_tokens=20,
                    total_tokens=30,
                    latency_ms=120,
                    status_code=200,
                    ttft_ms=150,
                    tps=10.0,
                ),
                RequestLog(
                    request_id="req-2",
                    trace_id="trace-2",
                    model_alias="gpt-4.2",
                    endpoint_id=endpoint.id,
                    api_key_id=api_key.id,
                    prompt_tokens=5,
                    completion_tokens=15,
                    total_tokens=20,
                    latency_ms=200,
                    status_code=200,
                    ttft_ms=250,
                    tps=20.0,
                ),
            ]
        )
        await session.commit()

        list_response = await client.get(
            "/admin/rules", headers={"Authorization": "Bearer token"}
        )

    assert list_response.status_code == 200
    data = list_response.json()
    alpha_rule = next(item for item in data if item["group_name"] == "alpha")
    assert alpha_rule["request_count"] == 2
    assert alpha_rule["total_tokens"] == 50
    assert alpha_rule["avg_ttft_ms"] == 200
    assert alpha_rule["avg_tps"] == 15.0
    assert alpha_rule["dump_enabled"] is True
    assert alpha_rule["dump_path"] == "/tmp/alpha-dump"
    default_rules = [item for item in data if item["group_name"] == "default"]
    assert len(default_rules) == 1
    assert default_rules[0]["model_pattern"] == ".*"

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_rules_list_bootstraps_default_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/rules", headers={"Authorization": "Bearer token"})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["group_name"] == "default"
        assert payload[0]["model_pattern"] == ".*"
        assert payload[0]["is_active"] is True
        assert payload[0]["target_key_ids"] == []
        default_rule_id = payload[0]["id"]

        second_response = await client.get(
            "/admin/rules", headers={"Authorization": "Bearer token"}
        )
        assert second_response.status_code == 200
        second_payload = second_response.json()
        default_rules = [item for item in second_payload if item["group_name"] == "default"]
        assert len(default_rules) == 1
        assert default_rules[0]["id"] == default_rule_id

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_default_rule_access_keys_and_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rules_response = await client.get(
            "/admin/rules", headers={"Authorization": "Bearer token"}
        )
        assert rules_response.status_code == 200
        default_rule_id = rules_response.json()[0]["id"]

        issue_response = await client.post(
            f"/admin/rules/{default_rule_id}/access-keys",
            headers={"Authorization": "Bearer token"},
            json={"name": "default-client"},
        )
        assert issue_response.status_code == 200
        issue_payload = issue_response.json()
        assert issue_payload["rule_id"] == default_rule_id
        assert issue_payload["name"] == "default-client"
        assert issue_payload["key"].startswith("rk-")

        list_response = await client.get(
            f"/admin/rules/{default_rule_id}/access-keys",
            headers={"Authorization": "Bearer token"},
        )
        assert list_response.status_code == 200
        list_payload = list_response.json()
        assert len(list_payload) == 1
        assert list_payload[0]["name"] == "default-client"
        assert list_payload[0]["key"].startswith("rk-")

        disable_response = await client.patch(
            f"/admin/rules/{default_rule_id}",
            headers={"Authorization": "Bearer token"},
            json={"is_active": False},
        )
        assert disable_response.status_code == 400
        assert (
            disable_response.json()["detail"]
            == "Default rule group cannot be disabled"
        )

        rename_response = await client.patch(
            f"/admin/rules/{default_rule_id}",
            headers={"Authorization": "Bearer token"},
            json={"group_name": "canary"},
        )
        assert rename_response.status_code == 400
        assert (
            rename_response.json()["detail"]
            == "Default rule group cannot be renamed"
        )

        delete_response = await client.delete(
            f"/admin/rules/{default_rule_id}",
            headers={"Authorization": "Bearer token"},
        )
        assert delete_response.status_code == 400
        assert (
            delete_response.json()["detail"]
            == "Default rule group cannot be deleted"
        )

    await session.close()
    await engine.dispose()

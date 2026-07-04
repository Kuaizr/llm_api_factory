import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.base import Base
from app.db.models import APIKey, Endpoint, FactoryAccessKey, ModelMap, RequestLog
from app.db.session import get_session
from app.services.access_keys import is_hashed_access_key


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

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
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
async def test_admin_rule_rejects_unsafe_model_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(
        master_auth_token="token",
        admin_legacy_master_bearer_enabled=True,
        proxy_dump_root="/tmp",
    )
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
                "model_pattern": "^(a+)+$",
                "group_name": "unsafe",
                "priority": 10,
                "target_key_ids": [],
            },
        )

    assert response.status_code == 400
    assert "Nested quantifiers" in response.json()["detail"]

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_rule_rejects_dump_path_outside_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    async def override_session():
        yield session

    settings = Settings(
        master_auth_token="token",
        admin_legacy_master_bearer_enabled=True,
        proxy_dump_root="/tmp/factory-dumps",
    )
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
                "group_name": "dump-check",
                "priority": 10,
                "target_key_ids": [],
                "dump_enabled": True,
                "dump_path": "/etc",
            },
        )

    assert response.status_code == 400
    assert "dump_path must stay under" in response.json()["detail"]

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

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
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

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
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
        assert issue_payload["key_preview"] != issue_payload["key"]
        stored_rule_key = await session.get(FactoryAccessKey, issue_payload["id"])
        assert stored_rule_key is not None
        assert stored_rule_key.key != issue_payload["key"]
        assert is_hashed_access_key(stored_rule_key.key)

        list_response = await client.get(
            f"/admin/rules/{default_rule_id}/access-keys",
            headers={"Authorization": "Bearer token"},
        )
        assert list_response.status_code == 200
        list_payload = list_response.json()
        assert len(list_payload) == 1
        assert list_payload[0]["name"] == "default-client"
        assert list_payload[0]["key"] is None
        assert list_payload[0]["key_preview"] == issue_payload["key_preview"]

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


@pytest.mark.asyncio
async def test_factory_access_key_lists_do_not_expose_full_key(
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
            "/admin/factory-keys",
            headers={"Authorization": "Bearer token"},
            json={"name": "client", "rule_groups": ["codex"]},
        )
        assert create_response.status_code == 200
        create_payload = create_response.json()
        assert create_payload["key"].startswith("fk-")
        stored_key = await session.get(FactoryAccessKey, create_payload["id"])
        assert stored_key is not None
        assert stored_key.key != create_payload["key"]
        assert is_hashed_access_key(stored_key.key)

        list_response = await client.get(
            "/admin/factory-keys",
            headers={"Authorization": "Bearer token"},
        )
        assert list_response.status_code == 200
        list_payload = list_response.json()
        assert len(list_payload) == 1
        assert list_payload[0]["key"] is None
        assert list_payload[0]["key_preview"] != create_payload["key"]

        update_response = await client.patch(
            f"/admin/factory-keys/{create_payload['id']}",
            headers={"Authorization": "Bearer token"},
            json={"name": "client-renamed"},
        )
        assert update_response.status_code == 200
        update_payload = update_response.json()
        assert update_payload["key"] is None
        assert update_payload["key_preview"] == list_payload[0]["key_preview"]

        rotate_response = await client.post(
            f"/admin/factory-keys/{create_payload['id']}/rotate",
            headers={"Authorization": "Bearer token"},
        )
        assert rotate_response.status_code == 200
        rotate_payload = rotate_response.json()
        assert rotate_payload["key"].startswith("fk-")
        assert rotate_payload["key"] != create_payload["key"]
        await session.refresh(stored_key)
        assert stored_key.key != rotate_payload["key"]
        assert is_hashed_access_key(stored_key.key)

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_endpoint_key_syncs_multi_rule_group_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_canary_rule = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4.*",
                "group_name": "canary",
                "priority": 20,
                "is_active": True,
                "target_key_ids": [],
            },
        )
        assert create_canary_rule.status_code == 200

        create_beta_rule = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4o-mini",
                "group_name": "beta",
                "priority": 15,
                "is_active": True,
                "target_key_ids": [],
            },
        )
        assert create_beta_rule.status_code == 200

        create_key_response = await client.post(
            f"/admin/endpoints/{endpoint.id}/keys",
            headers={"Authorization": "Bearer token"},
            json={
                "key": "sk-grouped",
                "name": "Grouped Key",
                "rule_groups": ["default", "canary"],
                "daily_limit": 100,
                "rpm_limit": 20,
                "is_active": True,
            },
        )
        assert create_key_response.status_code == 200
        create_payload = create_key_response.json()
        key_id = create_payload["id"]
        assert create_payload["rule_group"] == "canary"
        assert create_payload["rule_groups"] == ["default", "canary"]

        rules_response = await client.get(
            "/admin/rules", headers={"Authorization": "Bearer token"}
        )
        assert rules_response.status_code == 200
        rules_payload = rules_response.json()

    default_rule = next(item for item in rules_payload if item["group_name"] == "default")
    canary_rule = next(item for item in rules_payload if item["group_name"] == "canary")
    beta_rule = next(item for item in rules_payload if item["group_name"] == "beta")

    assert key_id in default_rule["target_key_ids"]
    assert key_id in canary_rule["target_key_ids"]
    assert key_id not in beta_rule["target_key_ids"]

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_key_resyncs_rule_group_targets(monkeypatch: pytest.MonkeyPatch) -> None:
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

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_canary_rule = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4.*",
                "group_name": "canary",
                "priority": 20,
                "is_active": True,
                "target_key_ids": [],
            },
        )
        assert create_canary_rule.status_code == 200

        create_beta_rule = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4o-mini",
                "group_name": "beta",
                "priority": 15,
                "is_active": True,
                "target_key_ids": [],
            },
        )
        assert create_beta_rule.status_code == 200

        create_key_response = await client.post(
            f"/admin/endpoints/{endpoint.id}/keys",
            headers={"Authorization": "Bearer token"},
            json={
                "key": "sk-grouped",
                "name": "Grouped Key",
                "rule_groups": ["default", "canary"],
                "is_active": True,
            },
        )
        assert create_key_response.status_code == 200
        key_id = create_key_response.json()["id"]

        update_key_response = await client.put(
            f"/admin/keys/{key_id}",
            headers={"Authorization": "Bearer token"},
            json={
                "rule_groups": ["default", "beta"],
                "rule_group": "beta",
            },
        )
        assert update_key_response.status_code == 200
        update_payload = update_key_response.json()
        assert update_payload["rule_group"] == "beta"
        assert update_payload["rule_groups"] == ["default", "beta"]

        rules_response = await client.get(
            "/admin/rules", headers={"Authorization": "Bearer token"}
        )
        assert rules_response.status_code == 200
        rules_payload = rules_response.json()

    default_rule = next(item for item in rules_payload if item["group_name"] == "default")
    canary_rule = next(item for item in rules_payload if item["group_name"] == "canary")
    beta_rule = next(item for item in rules_payload if item["group_name"] == "beta")

    assert key_id in default_rule["target_key_ids"]
    assert key_id not in canary_rule["target_key_ids"]
    assert key_id in beta_rule["target_key_ids"]

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_update_rule_target_keys_syncs_api_key_rule_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_key_response = await client.post(
            f"/admin/endpoints/{endpoint.id}/keys",
            headers={"Authorization": "Bearer token"},
            json={
                "key": "sk-rule-sync",
                "name": "Rule Sync Key",
                "rule_groups": ["default"],
                "is_active": True,
            },
        )
        assert create_key_response.status_code == 200
        key_id = create_key_response.json()["id"]

        create_rule_response = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4.*",
                "group_name": "canary",
                "priority": 20,
                "is_active": True,
                "target_key_ids": [],
            },
        )
        assert create_rule_response.status_code == 200
        rule_id = create_rule_response.json()["id"]

        add_key_response = await client.patch(
            f"/admin/rules/{rule_id}",
            headers={"Authorization": "Bearer token"},
            json={"target_key_ids": [key_id]},
        )
        assert add_key_response.status_code == 200

        keys_after_add = await client.get(
            f"/admin/api-keys?endpoint_id={endpoint.id}",
            headers={"Authorization": "Bearer token"},
        )
        assert keys_after_add.status_code == 200
        key_after_add = next(item for item in keys_after_add.json() if item["id"] == key_id)
        assert key_after_add["rule_group"] == "canary"
        assert key_after_add["rule_groups"] == ["default", "canary"]

        remove_key_response = await client.patch(
            f"/admin/rules/{rule_id}",
            headers={"Authorization": "Bearer token"},
            json={"target_key_ids": []},
        )
        assert remove_key_response.status_code == 200

        keys_after_remove = await client.get(
            f"/admin/api-keys?endpoint_id={endpoint.id}",
            headers={"Authorization": "Bearer token"},
        )
        assert keys_after_remove.status_code == 200
        key_after_remove = next(item for item in keys_after_remove.json() if item["id"] == key_id)
        assert key_after_remove["rule_group"] == "default"
        assert key_after_remove["rule_groups"] == ["default"]

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_rule_group_eligibility_auto_probes_when_model_maps_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    async def override_session():
        yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, proxy_dump_root="/tmp")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    captured_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_paths.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def override_http_client() -> httpx.AsyncClient:
        return upstream_client

    monkeypatch.setattr(routes_module, "get_http_client", override_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        create_rule_response = await client.post(
            "/admin/rules",
            headers={"Authorization": "Bearer token"},
            json={
                "model_pattern": "gpt-4.*",
                "group_name": "canary",
                "priority": 20,
                "is_active": True,
                "target_key_ids": [],
            },
        )
        assert create_rule_response.status_code == 200

        eligibility_response = await client.post(
            f"/admin/endpoints/{endpoint.id}/keys/check-rule-group",
            headers={"Authorization": "Bearer token"},
            json={
                "group_name": "canary",
                "api_key": "sk-probe-check",
            },
        )

    assert eligibility_response.status_code == 200
    payload = eligibility_response.json()
    assert payload["group_name"] == "canary"
    assert payload["eligible"] is True
    assert payload["probed"] is True
    assert payload["matched_models"] == ["gpt-4o-mini"]
    assert payload["required_patterns"] == ["gpt-4.*"]
    assert captured_paths == ["/v1/models"]

    model_maps = (
        await session.execute(select(ModelMap).where(ModelMap.endpoint_id == endpoint.id))
    ).scalars().all()
    assert len(model_maps) == 1
    assert model_maps[0].model_alias == "gpt-4o-mini"
    assert model_maps[0].probe_managed is True

    await upstream_client.aclose()
    await session.close()
    await engine.dispose()

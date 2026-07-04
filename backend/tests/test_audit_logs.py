import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.services.audit import audit_snapshot


def test_audit_snapshot_masks_secrets_but_keeps_routing_ids() -> None:
    snapshot = audit_snapshot(
        {
            "key": "sk-secret",
            "client_secret": "secret",
            "target_key_ids": [1, 2],
        }
    )

    assert snapshot["key"] == "********"
    assert snapshot["client_secret"] == "********"
    assert snapshot["target_key_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_admin_config_changes_write_masked_audit_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_maker() as session:
            yield session

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        endpoint_response = await client.post(
            "/admin/endpoints",
            headers={"Authorization": "Bearer token"},
            json={
                "name": "Custom",
                "base_url": "https://api.example.com",
                "provider": "custom",
                "oauth_config": {
                    "client_id": "client",
                    "client_secret": "top-secret",
                },
            },
        )
        assert endpoint_response.status_code == 200
        endpoint_id = endpoint_response.json()["id"]

        key_response = await client.post(
            "/admin/api-keys",
            headers={"Authorization": "Bearer token"},
            json={
                "endpoint_id": endpoint_id,
                "name": "primary",
                "key": "sk-live-secret",
            },
        )
        assert key_response.status_code == 200

        logs_response = await client.get(
            "/admin/audit-logs",
            headers={"Authorization": "Bearer token"},
        )

    assert logs_response.status_code == 200
    logs = logs_response.json()
    assert len(logs) == 2

    key_log = next(item for item in logs if item["resource_type"] == "api_key")
    assert key_log["action"] == "create"
    assert key_log["resource_name"] == "primary"
    assert key_log["after"]["key"] == "********"
    assert "sk-live-secret" not in str(key_log)

    endpoint_log = next(item for item in logs if item["resource_type"] == "endpoint")
    assert endpoint_log["action"] == "create"
    assert endpoint_log["resource_name"] == "Custom"
    assert endpoint_log["after"]["oauth_config"] == "********"
    assert "top-secret" not in str(endpoint_log)

    await engine.dispose()

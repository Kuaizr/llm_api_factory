from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.notifications import AlertPolicyStore


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


class FakeSession:
    async def execute(self, stmt):  # noqa: ANN001
        raise AssertionError("DB should not be hit in alert routes")


@pytest.mark.asyncio
async def test_admin_alert_routes_update_and_list(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = MemoryRedis()
    store = AlertPolicyStore(redis)

    async def fake_get_redis():
        return redis

    async def override_session():
        yield FakeSession()

    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/admin/alerts/circuit_open",
            headers={"Authorization": "Bearer token"},
            json={"enabled": True, "silence_minutes": 30},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event"] == "circuit_open"
        assert payload["enabled"] is True

        probe_response = await client.put(
            "/admin/alerts/probe_latency",
            headers={"Authorization": "Bearer token"},
            json={"enabled": True, "threshold_ms": 1500},
        )

        assert probe_response.status_code == 200
        probe_payload = probe_response.json()
        assert probe_payload["event"] == "probe_latency"
        assert probe_payload["threshold_ms"] == 1500

        list_response = await client.get(
            "/admin/alerts", headers={"Authorization": "Bearer token"}
        )

    assert list_response.status_code == 200
    items = list_response.json()
    assert any(item["event"] == "circuit_open" for item in items)

    saved_policy = await store.get_policy("circuit_open")
    assert saved_policy.enabled is True
    assert saved_policy.silence_until is not None
    assert saved_policy.silence_until > datetime.now(timezone.utc)

    probe_policy = await store.get_policy("probe_latency")
    assert probe_policy.threshold_ms == 1500

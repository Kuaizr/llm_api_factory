from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session


@dataclass
class AgentStub:
    id: int
    name: str
    region: str | None
    endpoint_url: str | None
    is_active: bool
    last_seen_at: datetime


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_agent_heartbeat_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, agent_auth_token="agent-token")
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    async def override_session():
        yield FakeSession()

    async def fake_upsert_agent(session, name, region, endpoint_url, now=None):
        return AgentStub(
            id=1,
            name=name,
            region=region,
            endpoint_url=endpoint_url,
            is_active=True,
            last_seen_at=now or datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "upsert_agent", fake_upsert_agent)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agent/heartbeat",
            headers={"Authorization": "Bearer agent-token"},
            json={"name": "edge-hk", "region": "hk", "endpoint_url": "https://edge"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "edge-hk"
    assert payload["region"] == "hk"
    assert payload["endpoint_url"] == "https://edge"
    assert payload["status"] == "online"


@pytest.mark.asyncio
async def test_agent_heartbeat_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True, agent_auth_token="agent-token")

    async def override_session():
        yield FakeSession()

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agent/heartbeat",
            json={"name": "edge-hk", "region": "hk"},
        )

    assert response.status_code == 401

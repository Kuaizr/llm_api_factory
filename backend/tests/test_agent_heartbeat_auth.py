from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.agents import hash_agent_token


@dataclass
class AgentStub:
    id: int
    name: str
    region: str | None
    endpoint_url: str | None
    is_active: bool
    last_seen_at: datetime | None
    auth_token_hash: str | None
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = None
    probe_checked_at: datetime | None = None


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_agent_heartbeat_rejects_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(agent_auth_token=None, agent_heartbeat_timeout_seconds=60)

    async def override_session():
        yield FakeSession()

    async def fake_get_agent_by_name(_session, _name):
        return AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            endpoint_url=None,
            is_active=True,
            last_seen_at=datetime.now(timezone.utc),
            auth_token_hash=hash_agent_token("secret"),
        )

    async def fake_upsert_agent(*_args, **_kwargs):
        return AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            endpoint_url=None,
            is_active=True,
            last_seen_at=datetime.now(timezone.utc),
            auth_token_hash=hash_agent_token("secret"),
        )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_agent_by_name", fake_get_agent_by_name)
    monkeypatch.setattr(routes_module, "upsert_agent", fake_upsert_agent)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agent/heartbeat",
            json={"name": "edge-hk", "token": "wrong"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_agent_heartbeat_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(agent_auth_token=None, agent_heartbeat_timeout_seconds=60)

    async def override_session():
        yield FakeSession()

    async def fake_get_agent_by_name(_session, _name):
        return AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            endpoint_url=None,
            is_active=True,
            last_seen_at=datetime.now(timezone.utc),
            auth_token_hash=hash_agent_token("secret"),
        )

    async def fake_upsert_agent(*_args, **_kwargs):
        return AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            endpoint_url=None,
            is_active=True,
            last_seen_at=datetime.now(timezone.utc),
            auth_token_hash=hash_agent_token("secret"),
        )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_agent_by_name", fake_get_agent_by_name)
    monkeypatch.setattr(routes_module, "upsert_agent", fake_upsert_agent)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/agent/heartbeat",
            json={"name": "edge-hk", "token": "secret"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "edge-hk"

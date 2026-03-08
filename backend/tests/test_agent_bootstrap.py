from __future__ import annotations

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
    last_seen_at: datetime | None


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_agent_bootstrap_returns_command(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        master_auth_token="token",
        agent_install_script_url="https://raw.githubusercontent.com/acme/llm/main/scripts/agent_install.sh",
    )

    async def override_session():
        yield FakeSession()

    async def fake_upsert_agent(*_args, **_kwargs):
        return AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            endpoint_url=None,
            is_active=True,
            last_seen_at=None,
        )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "issue_agent_token", lambda: "agent-token")
    monkeypatch.setattr(routes_module, "hash_agent_token", lambda _token: "hash")
    monkeypatch.setattr(routes_module, "upsert_agent", fake_upsert_agent)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/agents/bootstrap",
            headers={"Authorization": "Bearer token"},
            json={"name": "edge-hk"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] == "agent-token"
    assert "edge-hk" in payload["install_command"]
    assert "agent-token" in payload["install_command"]
    assert settings.agent_install_script_url in payload["install_command"]


@pytest.mark.asyncio
async def test_agent_bootstrap_falls_back_to_local_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(master_auth_token="token")

    async def override_session():
        yield FakeSession()

    async def fake_upsert_agent(*_args, **_kwargs):
        return AgentStub(
            id=1,
            name="edge-hk",
            region=None,
            endpoint_url=None,
            is_active=True,
            last_seen_at=None,
        )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "issue_agent_token", lambda: "agent-token")
    monkeypatch.setattr(routes_module, "hash_agent_token", lambda _token: "hash")
    monkeypatch.setattr(routes_module, "upsert_agent", fake_upsert_agent)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/agents/bootstrap",
            headers={"Authorization": "Bearer token"},
            json={"name": "edge-hk"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] == "agent-token"
    assert "http://test/agent/install.sh" in payload["install_command"]

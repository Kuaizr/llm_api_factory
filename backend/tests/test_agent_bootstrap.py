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
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
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
    assert "--ws-url ws://test/agent/ws" in payload["install_command"]
    assert "--heartbeat-url http://test/agent/heartbeat" in payload["install_command"]


@pytest.mark.asyncio
async def test_agent_bootstrap_falls_back_to_local_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)

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
    assert "--ws-url ws://test/agent/ws" in payload["install_command"]
    assert "--heartbeat-url http://test/agent/heartbeat" in payload["install_command"]


@pytest.mark.asyncio
async def test_agent_bootstrap_uses_public_base_and_repo_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        agent_public_base_url="https://factory.example.com/control",
        agent_install_script_url="https://raw.githubusercontent.com/Kuaizr/llm_api_factory/main/scripts/agent_install.sh",
        agent_install_repo_url="https://github.com/Kuaizr/llm_api_factory.git",
        agent_install_repo_ref="work/next",
    )

    async def override_session():
        yield FakeSession()

    async def fake_upsert_agent(*_args, **_kwargs):
        return AgentStub(
            id=1,
            name="edge-vps",
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
            json={"name": "edge-vps"},
        )

    assert response.status_code == 200
    command = response.json()["install_command"]
    assert settings.agent_install_script_url in command
    assert "--ws-url wss://factory.example.com/control/agent/ws" in command
    assert "--heartbeat-url https://factory.example.com/control/agent/heartbeat" in command
    assert "--repo https://github.com/Kuaizr/llm_api_factory.git" in command
    assert "--repo-ref work/next" in command

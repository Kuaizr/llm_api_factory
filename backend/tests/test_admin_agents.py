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
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = None
    probe_checked_at: datetime | None = None


class FakeSession:
    async def execute(self, stmt):  # noqa: ANN001
        return []


@pytest.mark.asyncio
async def test_admin_agents_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    agents = [
        AgentStub(
            id=1,
            name="edge-hk",
            region="hk",
            endpoint_url=None,
            is_active=True,
            last_seen_at=now,
        )
    ]
    settings = Settings(master_auth_token="token", agent_heartbeat_timeout_seconds=60)

    async def override_session():
        yield FakeSession()

    async def fake_list_agents(_session):
        return agents

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "list_agents", fake_list_agents)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/agents", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["status"] == "online"
    assert payload[0]["name"] == "edge-hk"

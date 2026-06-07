import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.base import Base
from app.db.models import APIKey, Agent, Endpoint, ModelMap, RoutingRule
from app.db.session import get_session
from app.services.agent_transport import get_agent_manager


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)

    async def delete(self, key: str) -> bool:
        self.store.pop(key, None)
        self.expirations.pop(key, None)
        return True


class FakeChannel:
    async def send_json(self, _payload):  # noqa: ANN001
        return None


@pytest.mark.asyncio
async def test_route_explain_reports_candidates_and_excluded_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    session = session_maker()

    direct = Endpoint(
        name="direct-openai",
        base_url="https://api.openai.com/v1",
        provider="openai",
        access_mode="direct",
        is_active=True,
    )
    via_agent = Endpoint(
        name="vps-openai",
        base_url="https://api.openai.com/v1",
        provider="openai",
        access_mode="via_agent",
        agent_node="edge-vps",
        is_active=True,
    )
    session.add_all([direct, via_agent])
    await session.commit()
    await session.refresh(direct)
    await session.refresh(via_agent)

    direct_key = APIKey(endpoint_id=direct.id, key="sk-direct", weight=1, is_active=True)
    agent_key = APIKey(endpoint_id=via_agent.id, key="sk-agent", weight=1, is_active=True)
    session.add_all([direct_key, agent_key])
    await session.commit()
    await session.refresh(direct_key)
    await session.refresh(agent_key)

    session.add_all(
        [
            ModelMap(endpoint_id=direct.id, model_alias="gpt-vps", real_model="gpt-4.1"),
            ModelMap(endpoint_id=via_agent.id, model_alias="gpt-vps", real_model="gpt-4.1"),
            RoutingRule(
                model_pattern="^gpt-vps$",
                group_name="vps",
                priority=10,
                is_active=True,
                target_key_ids_json=json.dumps(
                    {
                        "target_key_ids": [direct_key.id, agent_key.id],
                        "strategy": "sequential",
                    }
                ),
            ),
            Agent(name="edge-vps", region="us", is_active=False),
        ]
    )
    await session.commit()
    get_agent_manager().register("edge-vps", FakeChannel())

    async def override_session():
        yield session

    async def fake_get_redis():
        return MemoryRedis()

    monkeypatch.setattr(routes_module, "get_settings", lambda: Settings(master_auth_token="token"))
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/route-explain",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-vps", "rule_group": "vps"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_rule_group"] == "vps"
    assert payload["strategy"] == "sequential"
    assert payload["candidates"][0]["endpoint_name"] == "direct-openai"
    excluded = payload["excluded"][0]
    assert excluded["endpoint_name"] == "vps-openai"
    assert excluded["reasons"] == ["agent_disabled"]

    get_agent_manager().unregister("edge-vps")
    await session.close()
    await engine.dispose()

from dataclasses import dataclass
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import HealthProbeResult, HealthProbeStore


@dataclass
class EndpointStub:
    id: int
    name: str
    base_url: str
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    is_active: bool = True


@dataclass
class APIKeyStub:
    id: int
    endpoint_id: int
    rule_group: str
    is_active: bool = True


class FakeResult:
    def __init__(self, rows: list[tuple[APIKeyStub, EndpointStub]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[APIKeyStub, EndpointStub]]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[tuple[APIKeyStub, EndpointStub]]) -> None:
        self._rows = rows

    async def execute(self, stmt) -> FakeResult:  # noqa: ANN001
        return FakeResult(self._rows)


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(key) for key in keys]

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
        self.lists.pop(key, None)
        self.expirations.pop(key, None)
        return True

    async def lpush(self, key: str, value: str) -> int:
        items = self.lists.setdefault(key, [])
        items.insert(0, value)
        return len(items)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        items = self.lists.get(key, [])
        if end < 0:
            end = len(items) + end
        self.lists[key] = items[start : end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end < 0:
            end = len(items) + end
        return items[start : end + 1]


@pytest.mark.asyncio
async def test_health_status_endpoint_returns_probe_and_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=1, name="OpenAI", base_url="https://api.example.com")
    healthy_key = APIKeyStub(id=10, endpoint_id=1, rule_group="default")
    open_key = APIKeyStub(id=11, endpoint_id=1, rule_group="canary")

    session = FakeSession([(healthy_key, endpoint), (open_key, endpoint)])
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    await store.write(
        HealthProbeResult(
            api_key_id=healthy_key.id,
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
            real_model="gpt-4o",
            status="success",
            status_code=200,
            latency_ms=120,
            checked_at=datetime(2024, 1, 1, 0, 0, 0),
        )
    )

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=90,
    )
    breaker = CircuitBreaker(redis, settings=settings)
    await breaker.record_failure(open_key.id)

    async def override_session():
        yield session

    async def fake_get_redis():
        return redis

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/health-status", headers={"Authorization": "Bearer token"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2

    healthy_payload = next(item for item in payload if item["api_key_id"] == 10)
    assert healthy_payload["probe_status"] == "success"
    assert healthy_payload["circuit_state"] == "closed"

    open_payload = next(item for item in payload if item["api_key_id"] == 11)
    assert open_payload["probe_status"] == "unknown"
    assert open_payload["circuit_state"] == "open"

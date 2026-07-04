from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.health_monitor import HealthProbeResult, HealthProbeStore


class FakeScalars:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids

    def all(self) -> list[int]:
        return self._ids


class FakeResult:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._ids)


class FakeSession:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids

    async def execute(self, stmt) -> FakeResult:  # noqa: ANN001
        return FakeResult(self._ids)


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
async def test_health_probe_store_series_roundtrip() -> None:
    redis = MemoryRedis()
    store = HealthProbeStore(redis, ttl_seconds=60, series_ttl_seconds=3600, series_max_entries=10)
    now = datetime.now(timezone.utc)

    result_old = HealthProbeResult(
        api_key_id=1,
        endpoint_id=1,
        endpoint_name="OpenAI",
        real_model="gpt-4o",
        status="success",
        status_code=200,
        latency_ms=120,
        checked_at=now - timedelta(minutes=20),
    )
    result_new = HealthProbeResult(
        api_key_id=1,
        endpoint_id=1,
        endpoint_name="OpenAI",
        real_model="gpt-4o",
        status="failure",
        status_code=429,
        latency_ms=300,
        checked_at=now - timedelta(minutes=10),
    )

    await store.write(result_old)
    await store.write(result_new)

    series = await store.read_series(1, since=now - timedelta(hours=1))

    assert [item.status for item in series] == ["success", "failure"]


@pytest.mark.asyncio
async def test_admin_health_probe_timeseries(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = MemoryRedis()
    store = HealthProbeStore(redis, ttl_seconds=60, series_ttl_seconds=3600, series_max_entries=10)
    now = datetime(2024, 1, 1, 12, 30, tzinfo=timezone.utc)

    results = [
        HealthProbeResult(
            api_key_id=1,
            endpoint_id=1,
            endpoint_name="OpenAI",
            real_model="gpt-4o",
            status="success",
            status_code=200,
            latency_ms=100,
            checked_at=now - timedelta(minutes=10),
        ),
        HealthProbeResult(
            api_key_id=1,
            endpoint_id=1,
            endpoint_name="OpenAI",
            real_model="gpt-4o",
            status="failure",
            status_code=429,
            latency_ms=200,
            checked_at=now - timedelta(minutes=20),
        ),
        HealthProbeResult(
            api_key_id=1,
            endpoint_id=1,
            endpoint_name="OpenAI",
            real_model="gpt-4o",
            status="error",
            status_code=None,
            latency_ms=None,
            checked_at=now - timedelta(minutes=70),
        ),
    ]

    for result in results:
        await store.write(result)

    session = FakeSession([1])

    async def override_session():
        yield session

    async def fake_get_redis():
        return redis

    settings = Settings(
        master_auth_token="token", admin_legacy_master_bearer_enabled=True,
        health_probe_series_ttl_seconds=3600,
        health_probe_series_max_entries=10,
    )

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    since = (now - timedelta(hours=2)).isoformat()
    until = now.isoformat()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/health-status/timeseries",
            params={"bucket_minutes": 60, "since": since, "until": until},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    payload = response.json()
    buckets = {
        datetime.fromisoformat(item["bucket_start"].replace("Z", "+00:00")): item
        for item in payload
    }

    success_bucket_key = routes_module._floor_bucket(
        routes_module._normalize_datetime(results[0].checked_at), 3600
    )
    error_bucket_key = routes_module._floor_bucket(
        routes_module._normalize_datetime(results[2].checked_at), 3600
    )

    success_bucket = buckets[success_bucket_key]
    error_bucket = buckets[error_bucket_key]

    assert success_bucket["success_count"] == 1
    assert success_bucket["failure_count"] == 1
    assert success_bucket["error_count"] == 0
    assert success_bucket["avg_latency_ms"] == 150

    assert error_bucket["success_count"] == 0
    assert error_bucket["failure_count"] == 0
    assert error_bucket["error_count"] == 1
    assert error_bucket["avg_latency_ms"] is None

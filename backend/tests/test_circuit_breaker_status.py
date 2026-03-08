import pytest

from app.core.config import Settings
from app.services.circuit_breaker import CircuitBreaker


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


@pytest.mark.asyncio
async def test_get_status_reflects_failures_and_state() -> None:
    redis = MemoryRedis()
    settings = Settings(circuit_breaker_failures=2, circuit_breaker_ttl_seconds=60)
    breaker = CircuitBreaker(redis, settings=settings)

    status = await breaker.get_status(42)
    assert status.state == "closed"
    assert status.failures == 0

    await breaker.record_failure(42)
    status = await breaker.get_status(42)
    assert status.state == "closed"
    assert status.failures == 1

    await breaker.record_failure(42)
    status = await breaker.get_status(42)
    assert status.state == "open"
    assert status.failures == 2
    assert status.ttl_seconds == 60

    await breaker.record_success(42)
    status = await breaker.get_status(42)
    assert status.state == "closed"
    assert status.failures == 0

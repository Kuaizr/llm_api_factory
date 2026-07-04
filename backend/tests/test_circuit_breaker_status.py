import pytest

from app.core.config import Settings
from app.services.circuit_breaker import CircuitBreaker
from conftest import TestMemoryRedis as MemoryRedis


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


@pytest.mark.asyncio
async def test_are_available_batches_circuit_state_lookup() -> None:
    redis = MemoryRedis()
    breaker = CircuitBreaker(redis, settings=Settings())

    await redis.set("circuit:7:state", "open")

    assert await breaker.are_available([6, 7, 6]) == {6: True, 7: False}

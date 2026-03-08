from datetime import datetime, timedelta, timezone

import pytest

from app.services.notifications import AlertPolicyStore


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_alert_policy_defaults_and_silence() -> None:
    redis = MemoryRedis()
    store = AlertPolicyStore(redis)

    policy = await store.get_policy("circuit_open")
    assert policy.enabled is True
    assert policy.silence_until is None

    now = datetime.now(timezone.utc)
    silent_until = now + timedelta(minutes=30)
    await store.set_policy(
        "circuit_open", enabled=True, silence_until=silent_until, threshold_ms=None
    )

    assert await store.should_notify("circuit_open", now=now) is False
    assert await store.should_notify("circuit_open", now=silent_until) is True


@pytest.mark.asyncio
async def test_alert_policy_disable_blocks_notifications() -> None:
    redis = MemoryRedis()
    store = AlertPolicyStore(redis)

    await store.set_policy(
        "probe_latency", enabled=False, silence_until=None, threshold_ms=2500
    )

    assert await store.should_notify("probe_latency") is False


@pytest.mark.asyncio
async def test_alert_policy_threshold_roundtrip() -> None:
    redis = MemoryRedis()
    store = AlertPolicyStore(redis)

    await store.set_policy(
        "probe_latency", enabled=True, silence_until=None, threshold_ms=1800
    )

    policy = await store.get_policy("probe_latency")
    assert policy.threshold_ms == 1800

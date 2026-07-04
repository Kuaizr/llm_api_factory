from __future__ import annotations

import logging

import pytest

from app.core import redis as redis_module
from app.core.config import Settings
from app.core.redis import MemoryRedis


@pytest.mark.asyncio
async def test_memory_redis_mget_refreshes_lru_order() -> None:
    redis = MemoryRedis(max_keys=2)

    await redis.set("a", "1")
    await redis.set("b", "2")
    assert await redis.mget(["a"]) == ["1"]

    await redis.set("c", "3")

    assert await redis.get("a") == "1"
    assert await redis.get("b") is None
    assert await redis.get("c") == "3"


@pytest.mark.asyncio
async def test_memory_redis_expires_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_000.0
    monkeypatch.setattr(redis_module.time, "time", lambda: now)
    redis = MemoryRedis(max_keys=2)

    await redis.set("token", "value", ex=10)
    assert await redis.get("token") == "value"
    assert await redis.ttl("token") == 10

    now = 1_011.0
    assert await redis.get("token") is None
    assert await redis.ttl("token") == -2


@pytest.mark.asyncio
async def test_get_redis_warns_and_uses_bounded_memory_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingRedis:
        async def ping(self) -> bool:
            raise RuntimeError("connection refused")

    monkeypatch.setattr(redis_module, "_redis_client", None)
    monkeypatch.setattr(
        redis_module.Redis,
        "from_url",
        staticmethod(lambda *_args, **_kwargs: FailingRedis()),
    )
    monkeypatch.setattr(
        redis_module,
        "get_settings",
        lambda: Settings(
            redis_url="redis://redis.invalid:6379/0",
            memory_redis_max_keys=2,
        ),
    )

    with caplog.at_level(logging.WARNING):
        client = await redis_module.get_redis()

    assert isinstance(client, MemoryRedis)
    assert client.max_keys == 2
    assert "Redis unavailable, falling back to in-memory store" in caplog.text

    await redis_module.close_redis()

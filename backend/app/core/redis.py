from __future__ import annotations

import time
from typing import Any

from redis.asyncio import Redis

from app.core.config import get_settings


class MemoryRedis:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float | None]] = {}

    def _purge(self, key: str) -> None:
        item = self._store.get(key)
        if not item:
            return
        _, expires_at = item
        if expires_at is not None and expires_at <= time.time():
            del self._store[key]

    async def get(self, key: str) -> str | None:
        self._purge(key)
        item = self._store.get(key)
        value = item[0] if item else None
        return value if isinstance(value, str) else None

    async def mget(self, keys: list[str]) -> list[str | None]:
        values: list[str | None] = []
        for key in keys:
            self._purge(key)
            item = self._store.get(key)
            value = item[0] if item else None
            values.append(value if isinstance(value, str) else None)
        return values

    async def lpush(self, key: str, value: str) -> int:
        self._purge(key)
        item = self._store.get(key)
        expires_at = item[1] if item else None
        current = item[0] if item else []
        if not isinstance(current, list):
            current = []
        current.insert(0, str(value))
        self._store[key] = (current, expires_at)
        return len(current)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self._purge(key)
        item = self._store.get(key)
        if not item:
            return False
        value, expires_at = item
        if not isinstance(value, list):
            return False
        size = len(value)
        resolved_end = end if end >= 0 else size + end
        resolved_start = start if start >= 0 else size + start
        resolved_end = min(resolved_end, size - 1)
        resolved_start = max(resolved_start, 0)
        if resolved_start > resolved_end or size == 0:
            value = []
        else:
            value = value[resolved_start : resolved_end + 1]
        self._store[key] = (value, expires_at)
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        self._purge(key)
        item = self._store.get(key)
        if not item:
            return []
        value, _ = item
        if not isinstance(value, list):
            return []
        size = len(value)
        resolved_end = end if end >= 0 else size + end
        resolved_start = start if start >= 0 else size + start
        resolved_end = min(resolved_end, size - 1)
        resolved_start = max(resolved_start, 0)
        if resolved_start > resolved_end or size == 0:
            return []
        return [str(item) for item in value[resolved_start : resolved_end + 1]]

    async def set(
        self, key: str, value: Any, ex: int | None = None, nx: bool = False
    ) -> bool:
        self._purge(key)
        if nx and key in self._store:
            return False
        expires_at = time.time() + ex if ex is not None else None
        self._store[key] = (str(value), expires_at)
        return True

    async def incr(self, key: str) -> int:
        self._purge(key)
        item = self._store.get(key)
        current = int(item[0]) if item else 0
        next_value = current + 1
        expires_at = item[1] if item else None
        self._store[key] = (str(next_value), expires_at)
        return next_value

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self._purge(key)
        if key not in self._store:
            return False
        value, _ = self._store[key]
        self._store[key] = (value, time.time() + ttl_seconds)
        return True

    async def ttl(self, key: str) -> int:
        self._purge(key)
        item = self._store.get(key)
        if not item:
            return -2
        _, expires_at = item
        if expires_at is None:
            return -1
        ttl_value = int(expires_at - time.time())
        if ttl_value < 0:
            del self._store[key]
            return -2
        return ttl_value

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                deleted += 1
        return deleted

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._store.clear()


_redis_client: Redis | MemoryRedis | None = None


async def get_redis() -> Redis | MemoryRedis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await redis_client.ping()
        except Exception:
            _redis_client = MemoryRedis()
        else:
            _redis_client = redis_client
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None

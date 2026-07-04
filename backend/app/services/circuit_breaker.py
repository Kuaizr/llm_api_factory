from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.services.notifications import AlertPolicyStore
from app.services.telegram import TelegramNotifier


@dataclass(frozen=True)
class CircuitStatus:
    state: str
    failures: int
    ttl_seconds: int | None


class CircuitBreaker:
    def __init__(
        self,
        redis: Redis,
        notifier: TelegramNotifier | None = None,
        settings: Settings | None = None,
        alert_store: AlertPolicyStore | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self.redis = redis
        self.notifier = notifier
        self.failures_threshold = resolved_settings.circuit_breaker_failures
        self.ttl_seconds = resolved_settings.circuit_breaker_ttl_seconds
        self._alert_store = alert_store

    def _state_key(self, api_key_id: int) -> str:
        return f"circuit:{api_key_id}:state"

    def _fail_key(self, api_key_id: int) -> str:
        return f"circuit:{api_key_id}:failures"

    def _decode(self, value: str | bytes | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def is_available(self, api_key_id: int) -> bool:
        state = self._decode(await self.redis.get(self._state_key(api_key_id)))
        return state != "open"

    async def are_available(self, api_key_ids: list[int]) -> dict[int, bool]:
        unique_ids = list(dict.fromkeys(api_key_ids))
        if not unique_ids:
            return {}
        keys = [self._state_key(api_key_id) for api_key_id in unique_ids]
        states = await self.redis.mget(keys)
        return {
            api_key_id: self._decode(state) != "open"
            for api_key_id, state in zip(unique_ids, states, strict=False)
        }

    async def get_state(self, api_key_id: int) -> str | None:
        return self._decode(await self.redis.get(self._state_key(api_key_id)))

    async def get_status(self, api_key_id: int) -> CircuitStatus:
        state = self._decode(await self.redis.get(self._state_key(api_key_id)))
        failures_raw = self._decode(await self.redis.get(self._fail_key(api_key_id)))
        failures = int(failures_raw or 0)
        state_value = "open" if state == "open" else "closed"

        ttl_key = self._state_key(api_key_id) if state == "open" else self._fail_key(api_key_id)
        ttl_value = await self.redis.ttl(ttl_key)
        ttl_seconds = ttl_value if ttl_value >= 0 else None

        return CircuitStatus(state=state_value, failures=failures, ttl_seconds=ttl_seconds)

    async def record_failure(self, api_key_id: int) -> None:
        fail_key = self._fail_key(api_key_id)
        count = await self.redis.incr(fail_key)
        await self.redis.expire(fail_key, self.ttl_seconds)
        if count >= self.failures_threshold:
            opened = await self.redis.set(
                self._state_key(api_key_id), "open", ex=self.ttl_seconds, nx=True
            )
            if opened and self.notifier and await self._should_notify("circuit_open"):
                await self.notifier.send_message(
                    f"Circuit open for api_key_id={api_key_id} after {count} failures"
                )

    async def record_success(self, api_key_id: int) -> None:
        state = self._decode(await self.redis.get(self._state_key(api_key_id)))
        await self.redis.delete(self._fail_key(api_key_id))
        await self.redis.delete(self._state_key(api_key_id))
        if state == "open" and self.notifier and await self._should_notify("circuit_recovered"):
            await self.notifier.send_message(
                f"Circuit recovered for api_key_id={api_key_id}"
            )

    async def _should_notify(self, event: str) -> bool:
        if not self.notifier:
            return False
        store = self._alert_store or AlertPolicyStore(self.redis)
        return await store.should_notify(event)

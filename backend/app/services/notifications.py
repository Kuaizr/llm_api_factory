from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
from typing import Iterable

from redis.asyncio import Redis

from app.core.config import get_settings
from app.services.telegram import TelegramNotifier

ALERT_EVENTS = (
    "circuit_open",
    "circuit_recovered",
    "probe_latency",
    "probe_failure",
    "probe_error",
)


@dataclass(frozen=True)
class AlertPolicy:
    event: str
    enabled: bool
    silence_until: datetime | None
    threshold_ms: int | None = None

    def is_silenced(self, now: datetime | None = None) -> bool:
        if self.silence_until is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current < self.silence_until

    def to_payload(self) -> dict[str, str | bool | int | None]:
        return {
            "event": self.event,
            "enabled": self.enabled,
            "silence_until": self.silence_until.isoformat() if self.silence_until else None,
            "threshold_ms": self.threshold_ms,
        }

    @classmethod
    def from_payload(
        cls, payload: dict[str, str | bool | int | None]
    ) -> "AlertPolicy":
        silence_until = payload.get("silence_until")
        parsed: datetime | None = None
        if isinstance(silence_until, str) and silence_until:
            cleaned = silence_until
            if cleaned.endswith("Z"):
                cleaned = f"{cleaned[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(cleaned)
            except ValueError:
                parsed = None
        if parsed and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        threshold_ms = payload.get("threshold_ms")
        threshold_value = (
            int(threshold_ms)
            if isinstance(threshold_ms, int) and threshold_ms >= 0
            else None
        )
        return cls(
            event=str(payload.get("event") or ""),
            enabled=bool(payload.get("enabled", True)),
            silence_until=parsed,
            threshold_ms=threshold_value,
        )


class AlertPolicyStore:
    def __init__(self, redis: Redis, events: Iterable[str] = ALERT_EVENTS) -> None:
        self.redis = redis
        self.events = tuple(events)

    def _key(self, event: str) -> str:
        return f"alert:policy:{event}"

    async def get_policy(self, event: str) -> AlertPolicy:
        if event not in self.events:
            raise ValueError("unknown alert event")
        raw = await self.redis.get(self._key(event))
        if not raw:
            return AlertPolicy(event=event, enabled=True, silence_until=None, threshold_ms=None)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return AlertPolicy(event=event, enabled=True, silence_until=None, threshold_ms=None)
        if not isinstance(payload, dict):
            return AlertPolicy(event=event, enabled=True, silence_until=None, threshold_ms=None)
        payload["event"] = event
        return AlertPolicy.from_payload(payload)

    async def set_policy(
        self,
        event: str,
        enabled: bool,
        silence_until: datetime | None,
        threshold_ms: int | None,
    ) -> AlertPolicy:
        if event not in self.events:
            raise ValueError("unknown alert event")
        if silence_until and silence_until.tzinfo is None:
            silence_until = silence_until.replace(tzinfo=timezone.utc)
        policy = AlertPolicy(
            event=event,
            enabled=enabled,
            silence_until=silence_until,
            threshold_ms=threshold_ms,
        )
        await self.redis.set(self._key(event), json.dumps(policy.to_payload()))
        return policy

    async def list_policies(self) -> list[AlertPolicy]:
        policies = []
        for event in self.events:
            policies.append(await self.get_policy(event))
        return policies

    async def should_notify(self, event: str, now: datetime | None = None) -> bool:
        policy = await self.get_policy(event)
        if not policy.enabled:
            return False
        return not policy.is_silenced(now)


@lru_cache
def get_notifier() -> TelegramNotifier | None:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    return TelegramNotifier(
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=True,
    )

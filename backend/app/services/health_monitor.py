from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Iterable

from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.http_client import get_http_client
from app.core.redis import get_redis
from app.db.models import APIKey, Endpoint, ModelMap
from app.db.session import SessionLocal
from app.services.circuit_breaker import CircuitBreaker
from app.services.notifications import AlertPolicyStore, get_notifier
from app.services.telegram import TelegramNotifier


@dataclass(frozen=True)
class HealthTarget:
    endpoint: Endpoint
    api_key: APIKey
    real_model: str | None


@dataclass(frozen=True)
class HealthProbeResult:
    api_key_id: int
    endpoint_id: int
    endpoint_name: str
    real_model: str | None
    status: str
    status_code: int | None
    latency_ms: int | None
    checked_at: datetime

    def to_payload(self) -> dict[str, str | int | None]:
        return {
            "api_key_id": self.api_key_id,
            "endpoint_id": self.endpoint_id,
            "endpoint_name": self.endpoint_name,
            "real_model": self.real_model,
            "status": self.status,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "checked_at": self.checked_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, str | int | None]) -> "HealthProbeResult":
        checked_at_raw = payload.get("checked_at")
        checked_at = datetime.utcnow()
        if isinstance(checked_at_raw, str):
            cleaned = checked_at_raw
            if cleaned.endswith("Z"):
                cleaned = f"{cleaned[:-1]}+00:00"
            try:
                checked_at = datetime.fromisoformat(cleaned)
            except ValueError:
                checked_at = datetime.utcnow()
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return cls(
            api_key_id=int(payload.get("api_key_id") or 0),
            endpoint_id=int(payload.get("endpoint_id") or 0),
            endpoint_name=str(payload.get("endpoint_name") or ""),
            real_model=payload.get("real_model") if isinstance(payload.get("real_model"), str) else None,
            status=str(payload.get("status") or "unknown"),
            status_code=payload.get("status_code") if isinstance(payload.get("status_code"), int) else None,
            latency_ms=payload.get("latency_ms") if isinstance(payload.get("latency_ms"), int) else None,
            checked_at=checked_at,
        )


class HealthProbeStore:
    def __init__(
        self,
        redis: Redis,
        ttl_seconds: int | None = None,
        series_ttl_seconds: int | None = None,
        series_max_entries: int = 500,
    ) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.series_ttl_seconds = series_ttl_seconds
        self.series_max_entries = series_max_entries

    def _key(self, api_key_id: int) -> str:
        return f"health:probe:{api_key_id}"

    def _series_key(self, api_key_id: int) -> str:
        return f"health:probe:series:{api_key_id}"

    def _decode(self, value: str | bytes | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def write(self, result: HealthProbeResult) -> None:
        payload = json.dumps(result.to_payload())
        if self.ttl_seconds:
            await self.redis.set(self._key(result.api_key_id), payload, ex=self.ttl_seconds)
        else:
            await self.redis.set(self._key(result.api_key_id), payload)
        await self._append_series(result, payload)

    async def read(self, api_key_id: int) -> HealthProbeResult | None:
        raw = self._decode(await self.redis.get(self._key(api_key_id)))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return HealthProbeResult.from_payload(payload)

    async def read_many(self, api_key_ids: Iterable[int]) -> dict[int, HealthProbeResult]:
        ids = list(api_key_ids)
        if not ids:
            return {}
        raw_values = await self.redis.mget([self._key(api_key_id) for api_key_id in ids])
        results: dict[int, HealthProbeResult] = {}
        for api_key_id, raw in zip(ids, raw_values):
            decoded = self._decode(raw)
            if not decoded:
                continue
            try:
                payload = json.loads(decoded)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            results[api_key_id] = HealthProbeResult.from_payload(payload)
        return results

    async def read_series(
        self, api_key_id: int, since: datetime | None = None
    ) -> list[HealthProbeResult]:
        if self.series_max_entries <= 0:
            return []
        normalized_since = since
        if normalized_since and normalized_since.tzinfo is None:
            normalized_since = normalized_since.replace(tzinfo=timezone.utc)
        raw_values = await self.redis.lrange(
            self._series_key(api_key_id), 0, self.series_max_entries - 1
        )
        results: list[HealthProbeResult] = []
        for raw in raw_values:
            decoded = self._decode(raw)
            if not decoded:
                continue
            try:
                payload = json.loads(decoded)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            result = HealthProbeResult.from_payload(payload)
            if normalized_since and result.checked_at < normalized_since:
                continue
            results.append(result)
        results.sort(key=lambda item: item.checked_at)
        return results

    async def read_series_many(
        self, api_key_ids: Iterable[int], since: datetime | None = None
    ) -> dict[int, list[HealthProbeResult]]:
        ids = list(api_key_ids)
        if not ids:
            return {}
        series_list = await asyncio.gather(
            *[self.read_series(api_key_id, since) for api_key_id in ids]
        )
        return dict(zip(ids, series_list))

    async def _append_series(self, result: HealthProbeResult, payload: str) -> None:
        if self.series_max_entries <= 0:
            return
        key = self._series_key(result.api_key_id)
        await self.redis.lpush(key, payload)
        await self.redis.ltrim(key, 0, self.series_max_entries - 1)
        if self.series_ttl_seconds:
            await self.redis.expire(key, self.series_ttl_seconds)


def build_probe_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    return f"{cleaned}/v1/models"


class HealthMonitor:
    def __init__(
        self,
        client: AsyncClient | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        notifier: TelegramNotifier | None = None,
        redis: Redis | None = None,
        probe_store: HealthProbeStore | None = None,
        settings: Settings | None = None,
        session_factory=SessionLocal,
    ) -> None:
        self.settings = settings or get_settings()
        self.logger = logging.getLogger(__name__)
        self._stop_event = asyncio.Event()
        self._client = client
        self._circuit_breaker = circuit_breaker
        self._notifier = notifier
        self._redis = redis
        self._probe_store = probe_store
        self._session_factory = session_factory

    async def run(self) -> None:
        while not self._stop_event.is_set():
            if self.settings.health_probe_enabled:
                try:
                    await self.run_once()
                except Exception:
                    self.logger.exception("health_probe_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.health_probe_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        self._stop_event.set()

    async def run_once(self) -> None:
        async with self._session_factory() as session:
            targets = await self._collect_targets(session)

        if not targets:
            return

        for target in targets:
            await self.probe_target(target)

    async def probe_target(self, target: HealthTarget) -> None:
        client = await self._get_client()
        redis = await self._get_redis()
        notifier = self._notifier or get_notifier()
        circuit_breaker = self._circuit_breaker or CircuitBreaker(redis, notifier=notifier)
        probe_store = self._probe_store or HealthProbeStore(
            redis,
            ttl_seconds=self.settings.health_probe_result_ttl_seconds,
            series_ttl_seconds=self.settings.health_probe_series_ttl_seconds,
            series_max_entries=self.settings.health_probe_series_max_entries,
        )
        alert_store = AlertPolicyStore(probe_store.redis)

        url = build_probe_url(target.endpoint.base_url)
        headers = self._build_headers(target)
        start = time.perf_counter()
        status_code: int | None = None
        latency_ms: int | None = None
        status = "error"

        try:
            response = await client.get(
                url,
                headers=headers,
                timeout=self.settings.health_probe_timeout_seconds,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            status_code = response.status_code
            await response.aclose()

            if status_code >= 400:
                status = "failure"
                await circuit_breaker.record_failure(target.api_key.id)
            else:
                status = "success"
                await circuit_breaker.record_success(target.api_key.id)
        except Exception:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await circuit_breaker.record_failure(target.api_key.id)

        result = HealthProbeResult(
            api_key_id=target.api_key.id,
            endpoint_id=target.endpoint.id,
            endpoint_name=target.endpoint.name,
            real_model=target.real_model,
            status=status,
            status_code=status_code,
            latency_ms=latency_ms,
            checked_at=datetime.utcnow(),
        )
        await probe_store.write(result)

        policy = await alert_store.get_policy("probe_latency")
        threshold_ms = (
            policy.threshold_ms
            if policy.threshold_ms is not None
            else self.settings.health_probe_latency_threshold_ms
        )
        if (
            notifier
            and status == "success"
            and latency_ms is not None
            and threshold_ms > 0
            and latency_ms > threshold_ms
            and policy.enabled
            and not policy.is_silenced()
        ):
            await notifier.send_message(
                "Health probe latency alert for "
                f"endpoint={target.endpoint.name} api_key_id={target.api_key.id} "
                f"latency_ms={latency_ms}"
            )

        if notifier and status in {"failure", "error"}:
            event = "probe_failure" if status == "failure" else "probe_error"
            if await alert_store.should_notify(event):
                detail = f"status_code={status_code}" if status == "failure" else ""
                detail_text = f" {detail}" if detail else ""
                await notifier.send_message(
                    f"Health probe {status} alert for "
                    f"endpoint={target.endpoint.name} api_key_id={target.api_key.id}"
                    f"{detail_text}"
                )

    async def _collect_targets(self, session: AsyncSession) -> list[HealthTarget]:
        stmt = (
            select(APIKey, Endpoint, ModelMap)
            .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
            .outerjoin(ModelMap, ModelMap.endpoint_id == Endpoint.id)
            .where(APIKey.is_active.is_(True), Endpoint.is_active.is_(True))
            .order_by(ModelMap.id)
        )
        result = await session.execute(stmt)
        targets: list[HealthTarget] = []
        seen: set[int] = set()
        for api_key, endpoint, model_map in result.all():
            if api_key.id in seen:
                continue
            seen.add(api_key.id)
            targets.append(
                HealthTarget(
                    endpoint=endpoint,
                    api_key=api_key,
                    real_model=model_map.real_model if model_map else None,
                )
            )
        return targets

    def _build_headers(self, target: HealthTarget) -> dict[str, str]:
        header_name = target.endpoint.auth_header_name or "Authorization"
        header_prefix = target.endpoint.auth_header_prefix
        if header_prefix:
            value = f"{header_prefix} {target.api_key.key}"
        else:
            value = target.api_key.key
        return {header_name: value, "Content-Type": "application/json"}

    async def _get_client(self) -> AsyncClient:
        if self._client is not None:
            return self._client
        return await get_http_client()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        return await get_redis()

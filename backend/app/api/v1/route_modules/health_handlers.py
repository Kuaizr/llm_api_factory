from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import (
    _normalize_datetime,
    _parse_iso_datetime,
    build_health_probe_buckets,
)
from app.api.v1.route_models import (
    AlertPolicyOut,
    AlertPolicyUpdate,
    HealthProbeBucketOut,
    HealthStatusOut,
    TelegramConfigOut,
    TelegramConfigUpdate,
    TelegramTestOut,
)
from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.models import APIKey, Endpoint
from app.db.session import get_session
from app.services.audit import record_audit_log
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import HealthProbeStore
from app.services.notifications import ALERT_EVENTS, AlertPolicyStore, get_notifier


async def admin_alert_policies() -> list[AlertPolicyOut]:
    redis = await get_redis()
    store = AlertPolicyStore(redis)
    policies = await store.list_policies()
    return [
        AlertPolicyOut(
            event=policy.event,
            enabled=policy.enabled,
            silence_until=policy.silence_until,
            threshold_ms=policy.threshold_ms,
        )
        for policy in policies
    ]


async def admin_alert_policy_update(
    event: str,
    payload: AlertPolicyUpdate,
    session: AsyncSession = Depends(get_session),
) -> AlertPolicyOut:
    if event not in ALERT_EVENTS:
        raise HTTPException(status_code=404, detail="Unknown alert event")
    redis = await get_redis()
    store = AlertPolicyStore(redis)
    current = await store.get_policy(event)
    before = {
        "event": current.event,
        "enabled": current.enabled,
        "silence_until": current.silence_until,
        "threshold_ms": current.threshold_ms,
    }

    enabled = payload.enabled if payload.enabled is not None else current.enabled
    silence_until = payload.silence_until
    if payload.silence_minutes is not None:
        silence_until = (
            datetime.now(timezone.utc) + timedelta(minutes=payload.silence_minutes)
            if payload.silence_minutes > 0
            else None
        )
    if silence_until and silence_until.tzinfo is None:
        silence_until = silence_until.replace(tzinfo=timezone.utc)

    threshold_update = "threshold_ms" in payload.model_fields_set
    if threshold_update and event != "probe_latency":
        raise HTTPException(status_code=400, detail="Threshold only supported for probe_latency")
    threshold_ms = current.threshold_ms
    if threshold_update:
        threshold_ms = payload.threshold_ms

    policy = await store.set_policy(
        event,
        enabled=enabled,
        silence_until=silence_until,
        threshold_ms=threshold_ms,
    )
    await record_audit_log(
        session,
        action="update",
        resource_type="alert_policy",
        resource_id=event,
        resource_name=event,
        before=before,
        after={
            "event": policy.event,
            "enabled": policy.enabled,
            "silence_until": policy.silence_until,
            "threshold_ms": policy.threshold_ms,
        },
    )
    await session.commit()
    return AlertPolicyOut(
        event=policy.event,
        enabled=policy.enabled,
        silence_until=policy.silence_until,
        threshold_ms=policy.threshold_ms,
    )


def _mask_bot_token(token: str | None) -> str | None:
    if not token:
        return None
    trimmed = token.strip()
    if len(trimmed) <= 8:
        return "*" * len(trimmed)
    return f"{trimmed[:4]}...{trimmed[-4:]}"


def _build_telegram_config_out() -> TelegramConfigOut:
    settings = get_settings()
    token = (settings.telegram_bot_token or "").strip() or None
    chat_id = (settings.telegram_chat_id or "").strip() or None
    return TelegramConfigOut(
        configured=bool(token and chat_id),
        bot_token_masked=_mask_bot_token(token),
        chat_id=chat_id,
    )


async def admin_telegram_config() -> TelegramConfigOut:
    return _build_telegram_config_out()


async def admin_telegram_config_update(
    payload: TelegramConfigUpdate,
    session: AsyncSession = Depends(get_session),
) -> TelegramConfigOut:
    settings = get_settings()
    before = {
        "bot_token": settings.telegram_bot_token,
        "chat_id": settings.telegram_chat_id,
    }

    if "bot_token" in payload.model_fields_set:
        next_token = (payload.bot_token or "").strip()
        settings.telegram_bot_token = next_token or None

    if "chat_id" in payload.model_fields_set:
        next_chat_id = (payload.chat_id or "").strip()
        settings.telegram_chat_id = next_chat_id or None

    get_notifier.cache_clear()
    await record_audit_log(
        session,
        action="update",
        resource_type="telegram_config",
        resource_id="default",
        resource_name="telegram",
        before=before,
        after={
            "bot_token": settings.telegram_bot_token,
            "chat_id": settings.telegram_chat_id,
        },
    )
    await session.commit()
    return _build_telegram_config_out()


async def admin_telegram_test() -> TelegramTestOut:
    notifier = get_notifier()
    if not notifier:
        raise HTTPException(status_code=400, detail="Telegram Bot 未配置，请先保存 Bot Token 与 Chat ID")
    await notifier.send_message(
        f"LLM API Factory 测试通知 {datetime.now(timezone.utc).isoformat()}"
    )
    return TelegramTestOut(detail="测试消息已发送")


async def admin_health_probe_timeseries(
    hours: int = Query(default=24, ge=1, le=168),
    bucket_minutes: int = Query(default=30, ge=1, le=1440),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[HealthProbeBucketOut]:
    end_time = _parse_iso_datetime(until) or datetime.now(timezone.utc)
    start_time = _parse_iso_datetime(since) or end_time - timedelta(hours=hours)
    if start_time > end_time:
        raise HTTPException(status_code=400, detail="since must be before until")

    start_time = _normalize_datetime(start_time)
    end_time = _normalize_datetime(end_time)

    result = await session.execute(select(APIKey.id))
    api_key_ids = result.scalars().all()

    redis = await get_redis()
    settings = get_settings()
    store = HealthProbeStore(
        redis,
        ttl_seconds=settings.health_probe_result_ttl_seconds,
        series_ttl_seconds=settings.health_probe_series_ttl_seconds,
        series_max_entries=settings.health_probe_series_max_entries,
    )
    series_map = await store.read_series_many(api_key_ids, start_time)
    results = [item for series in series_map.values() for item in series]
    if not results:
        return []

    return build_health_probe_buckets(results, start_time, end_time, bucket_minutes)


async def admin_health_status(
    session: AsyncSession = Depends(get_session),
) -> list[HealthStatusOut]:
    redis = await get_redis()
    circuit_breaker = CircuitBreaker(redis)
    probe_store = HealthProbeStore(redis)

    stmt = (
        select(APIKey, Endpoint)
        .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
        .order_by(APIKey.id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    api_key_ids = [api_key.id for api_key, _ in rows]
    probe_results = await probe_store.read_many(api_key_ids)

    statuses: list[HealthStatusOut] = []
    for api_key, endpoint in rows:
        probe = probe_results.get(api_key.id)
        circuit_status = await circuit_breaker.get_status(api_key.id)
        statuses.append(
            HealthStatusOut(
                api_key_id=api_key.id,
                endpoint_id=endpoint.id,
                endpoint_name=endpoint.name,
                rule_group=getattr(api_key, "primary_rule_group", api_key.rule_group),
                is_active=api_key.is_active,
                probe_status=probe.status if probe else "unknown",
                probe_status_code=probe.status_code if probe else None,
                probe_latency_ms=probe.latency_ms if probe else None,
                probe_checked_at=probe.checked_at if probe else None,
                probe_real_model=probe.real_model if probe else None,
                circuit_state=circuit_status.state,
                circuit_failures=circuit_status.failures,
                circuit_ttl_seconds=circuit_status.ttl_seconds,
            )
        )

    return statuses

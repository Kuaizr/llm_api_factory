from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import (
    _build_dashboard_endpoint,
    _mask_key,
    _normalize_datetime,
    _parse_iso_datetime,
    build_metric_buckets,
)
from app.api.v1.route_models import (
    DashboardAgentOut,
    DashboardStatusOut,
    MetricsBucketOut,
    OverviewOut,
    RouteCandidateOut,
    RouteTestRequest,
    RouteTestResponse,
    UsageGroupStat,
    UsageStatsOut,
    UsageTopKey,
)
from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.models import APIKey, Endpoint, ModelMap, RequestLog
from app.db.session import get_session
from app.services.agents import build_agent_statuses, list_agents
from app.services.circuit_breaker import CircuitBreaker
from app.services.notifications import get_notifier
from app.services.router import ModelRouter


async def public_dashboard(
    session: AsyncSession = Depends(get_session),
) -> DashboardStatusOut:
    settings = get_settings()
    result = await session.execute(select(Endpoint).order_by(Endpoint.id))
    endpoints = result.scalars().all()
    agents = await list_agents(session)
    agent_statuses = build_agent_statuses(
        agents, datetime.now(timezone.utc), settings.agent_heartbeat_timeout_seconds
    )
    return DashboardStatusOut(
        endpoints=[_build_dashboard_endpoint(endpoint) for endpoint in endpoints],
        agents=[
            DashboardAgentOut(
                id=status.id,
                name=status.name,
                region=status.region,
                status=status.status,
                last_seen_at=status.last_seen_at,
                endpoint_url=status.endpoint_url,
            )
            for status in agent_statuses
        ],
        generated_at=datetime.now(timezone.utc),
    )


async def admin_overview(session: AsyncSession = Depends(get_session)) -> OverviewOut:
    endpoints = await session.scalar(select(func.count()).select_from(Endpoint)) or 0
    api_keys = await session.scalar(select(func.count()).select_from(APIKey)) or 0
    model_maps = await session.scalar(select(func.count()).select_from(ModelMap)) or 0
    request_logs = await session.scalar(select(func.count()).select_from(RequestLog)) or 0
    return OverviewOut(
        endpoints=endpoints,
        api_keys=api_keys,
        model_maps=model_maps,
        request_logs=request_logs,
        generated_at=datetime.now(timezone.utc),
    )


async def admin_usage_stats(
    session: AsyncSession = Depends(get_session),
) -> UsageStatsOut:
    stmt = (
        select(RequestLog, APIKey, Endpoint)
        .join(APIKey, RequestLog.api_key_id == APIKey.id)
        .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    group_totals: dict[str, int] = {}
    key_totals: dict[int, dict[str, int]] = {}
    total_tokens = 0
    total_tokens_today = 0
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for log, api_key, endpoint in rows:
        tokens = log.total_tokens
        if tokens is None:
            tokens = (log.prompt_tokens or 0) + (log.completion_tokens or 0)
        group_name = (
            log.rule_group
            or getattr(api_key, "primary_rule_group", api_key.rule_group)
            or "default"
        )
        group_totals[group_name] = group_totals.get(group_name, 0) + tokens
        total_tokens += tokens
        log_time = _normalize_datetime(log.created_at)
        if log_time >= today_start:
            total_tokens_today += tokens
        key_data = key_totals.setdefault(
            api_key.id,
            {
                "tokens": 0,
                "endpoint_name": endpoint.name,
                "key_preview": _mask_key(api_key.key),
            },
        )
        key_data["tokens"] += tokens

    groups: list[UsageGroupStat] = []
    for group_name, tokens in group_totals.items():
        percent = (tokens / total_tokens * 100) if total_tokens else 0.0
        groups.append(
            UsageGroupStat(group_name=group_name, percent=percent, total_tokens=tokens)
        )
    groups.sort(key=lambda item: item.total_tokens, reverse=True)

    top_keys: list[UsageTopKey] = []
    for api_key_id, data in sorted(
        key_totals.items(), key=lambda item: item[1]["tokens"], reverse=True
    )[:5]:
        top_keys.append(
            UsageTopKey(
                api_key_id=api_key_id,
                endpoint_name=data["endpoint_name"],
                key_preview=data["key_preview"],
                total_tokens=data["tokens"],
            )
        )

    return UsageStatsOut(
        groups=groups,
        top_keys=top_keys,
        total_tokens_today=total_tokens_today,
        generated_at=datetime.now(timezone.utc),
    )


async def admin_metrics_timeseries(
    hours: int = Query(default=24, ge=1, le=8760),
    bucket_minutes: int = Query(default=60, ge=1, le=10080),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[MetricsBucketOut]:
    end_time = _parse_iso_datetime(until) or datetime.now(timezone.utc)
    start_time = _parse_iso_datetime(since) or end_time - timedelta(hours=hours)
    if start_time > end_time:
        raise HTTPException(status_code=400, detail="since must be before until")

    stmt = select(RequestLog).where(
        RequestLog.created_at >= start_time, RequestLog.created_at <= end_time
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()
    return build_metric_buckets(logs, start_time, end_time, bucket_minutes)


async def route_test(
    payload: RouteTestRequest, session: AsyncSession = Depends(get_session)
) -> RouteTestResponse:
    redis = await get_redis()
    notifier = get_notifier()
    circuit_breaker = CircuitBreaker(redis, notifier=notifier)
    router_service = ModelRouter(circuit_breaker)
    candidates, effective_group = await router_service.get_candidates(
        session, payload.model, payload.rule_group
    )
    ordered = [
        RouteCandidateOut(
            order=index + 1,
            endpoint_id=candidate.endpoint.id,
            endpoint_name=candidate.endpoint.name,
            api_key_id=candidate.api_key.id,
            weight=candidate.api_key.weight,
            real_model=candidate.real_model,
        )
        for index, candidate in enumerate(candidates)
    ]
    return RouteTestResponse(
        model=payload.model,
        rule_group=effective_group,
        candidates=ordered,
    )

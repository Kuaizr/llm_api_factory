from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_modules.admin_handlers import _deserialize_rule_config
from app.api.v1.route_helpers import (
    _build_dashboard_endpoint,
    _floor_bucket,
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
    RouteExplainCandidateOut,
    RouteExplainExcludedOut,
    RouteExplainResponse,
    RouteTestRequest,
    RouteTestResponse,
    UsageGroupStat,
    UsageStatsOut,
    UsageTopKey,
    DumpSearchItemOut,
    DumpSearchOut,
    StatsDistributionItemOut,
    StatsKpiValue,
    StatsLatencyPercentileBucketOut,
    StatsOverviewOut,
    StatsTimeseriesBucketOut,
    StatsTopKeyOut,
)
from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.models import APIKey, Agent, DumpIndex, Endpoint, ModelMap, RequestLog, RoutingRule
from app.db.session import get_session
from app.services.agent_transport import get_agent_manager
from app.services.agents import build_agent_statuses, list_agents
from app.services.circuit_breaker import CircuitBreaker
from app.services.model_patterns import model_pattern_matches
from app.services.notifications import get_notifier
from app.services.router import ModelRouter, RouteCandidate


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


def _stats_time_window(
    hours: int,
    since: str | None,
    until: str | None,
) -> tuple[datetime, datetime]:
    end_time = _parse_iso_datetime(until) or datetime.now(timezone.utc)
    start_time = _parse_iso_datetime(since) or end_time - timedelta(hours=hours)
    if start_time > end_time:
        raise HTTPException(status_code=400, detail="since must be before until")
    return _normalize_datetime(start_time), _normalize_datetime(end_time)


def _log_total_tokens(log: RequestLog) -> int:
    if log.total_tokens is not None:
        return log.total_tokens
    return (log.prompt_tokens or 0) + (log.completion_tokens or 0)


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return int(ordered[index])


def _change_percent(current: float, previous: float) -> float | None:
    if previous == 0:
        return 0.0 if current == 0 else None
    return (current - previous) / previous * 100


def _kpi(current: float, previous: float) -> StatsKpiValue:
    return StatsKpiValue(
        value=current,
        previous_value=previous,
        change_percent=_change_percent(current, previous),
    )


async def _stats_rows(
    session: AsyncSession,
    start_time: datetime,
    end_time: datetime,
) -> list[tuple[RequestLog, APIKey | None, Endpoint | None, DumpIndex | None]]:
    stmt = (
        select(RequestLog, APIKey, Endpoint, DumpIndex)
        .outerjoin(APIKey, RequestLog.api_key_id == APIKey.id)
        .outerjoin(Endpoint, RequestLog.endpoint_id == Endpoint.id)
        .outerjoin(DumpIndex, DumpIndex.request_id == RequestLog.request_id)
        .where(RequestLog.created_at >= start_time, RequestLog.created_at <= end_time)
        .order_by(RequestLog.created_at)
    )
    result = await session.execute(stmt)
    return list(result.all())


def _aggregate_rows(
    rows: list[tuple[RequestLog, APIKey | None, Endpoint | None, DumpIndex | None]]
) -> dict[str, float | int | None]:
    request_count = len(rows)
    prompt_tokens = sum(log.prompt_tokens or 0 for log, *_ in rows)
    completion_tokens = sum(log.completion_tokens or 0 for log, *_ in rows)
    total_tokens = sum(_log_total_tokens(log) for log, *_ in rows)
    cached_tokens = sum((dump.cached_tokens or 0) for *_, dump in rows if dump is not None)
    cache_hits = sum(
        1 for *_, dump in rows if dump is not None and bool(dump.is_cache_hit)
    )
    latency_values = [log.latency_ms for log, *_ in rows if log.latency_ms is not None]
    avg_latency = (
        int(sum(latency_values) / len(latency_values)) if latency_values else None
    )
    return {
        "request_count": request_count,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "cache_hits": cache_hits,
        "cache_hit_rate": (cache_hits / request_count * 100) if request_count else 0.0,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": _percentile(latency_values, 0.95),
    }


async def admin_stats_overview(
    hours: int = Query(default=24, ge=1, le=8760),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> StatsOverviewOut:
    start_time, end_time = _stats_time_window(hours, since, until)
    duration = end_time - start_time
    previous_start = start_time - duration
    rows = await _stats_rows(session, start_time, end_time)
    previous_rows = await _stats_rows(session, previous_start, start_time)
    current = _aggregate_rows(rows)
    previous = _aggregate_rows(previous_rows)
    return StatsOverviewOut(
        total_requests=_kpi(
            float(current["request_count"] or 0),
            float(previous["request_count"] or 0),
        ),
        total_tokens=_kpi(
            float(current["total_tokens"] or 0),
            float(previous["total_tokens"] or 0),
        ),
        cache_hit_rate=_kpi(
            float(current["cache_hit_rate"] or 0.0),
            float(previous["cache_hit_rate"] or 0.0),
        ),
        avg_latency_ms=_kpi(
            float(current["avg_latency_ms"] or 0),
            float(previous["avg_latency_ms"] or 0),
        ),
        prompt_tokens=int(current["prompt_tokens"] or 0),
        completion_tokens=int(current["completion_tokens"] or 0),
        cached_tokens=int(current["cached_tokens"] or 0),
        p95_latency_ms=(
            int(current["p95_latency_ms"]) if current["p95_latency_ms"] is not None else None
        ),
        generated_at=datetime.now(timezone.utc),
    )


def _bucket_keys(
    start_time: datetime,
    end_time: datetime,
    bucket_minutes: int,
) -> dict[datetime, dict[str, object]]:
    bucket_seconds = bucket_minutes * 60
    bucket_start = _floor_bucket(start_time, bucket_seconds)
    buckets: dict[datetime, dict[str, object]] = {}
    while bucket_start <= end_time:
        buckets[bucket_start] = {
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "cache_hits": 0,
            "latencies": [],
        }
        bucket_start += timedelta(seconds=bucket_seconds)
    return buckets


async def admin_stats_timeseries(
    hours: int = Query(default=24, ge=1, le=8760),
    bucket_minutes: int = Query(default=60, ge=1, le=10080),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[StatsTimeseriesBucketOut]:
    start_time, end_time = _stats_time_window(hours, since, until)
    rows = await _stats_rows(session, start_time, end_time)
    buckets = _bucket_keys(start_time, end_time, bucket_minutes)
    bucket_seconds = bucket_minutes * 60
    for log, _api_key, _endpoint, dump in rows:
        bucket = buckets.get(_floor_bucket(_normalize_datetime(log.created_at), bucket_seconds))
        if bucket is None:
            continue
        bucket["request_count"] = int(bucket["request_count"]) + 1
        bucket["prompt_tokens"] = int(bucket["prompt_tokens"]) + (log.prompt_tokens or 0)
        bucket["completion_tokens"] = int(bucket["completion_tokens"]) + (
            log.completion_tokens or 0
        )
        bucket["total_tokens"] = int(bucket["total_tokens"]) + _log_total_tokens(log)
        bucket["cached_tokens"] = int(bucket["cached_tokens"]) + (
            dump.cached_tokens or 0 if dump is not None else 0
        )
        if dump is not None and dump.is_cache_hit:
            bucket["cache_hits"] = int(bucket["cache_hits"]) + 1
        latencies = bucket["latencies"]
        if isinstance(latencies, list):
            latencies.append(log.latency_ms)

    results: list[StatsTimeseriesBucketOut] = []
    for bucket_start, data in sorted(buckets.items()):
        count = int(data["request_count"])
        latencies = data["latencies"] if isinstance(data["latencies"], list) else []
        results.append(
            StatsTimeseriesBucketOut(
                bucket_start=bucket_start,
                request_count=count,
                prompt_tokens=int(data["prompt_tokens"]),
                completion_tokens=int(data["completion_tokens"]),
                total_tokens=int(data["total_tokens"]),
                cached_tokens=int(data["cached_tokens"]),
                cache_hits=int(data["cache_hits"]),
                cache_hit_rate=(int(data["cache_hits"]) / count * 100) if count else 0.0,
                avg_latency_ms=(
                    int(sum(latencies) / len(latencies)) if latencies else None
                ),
            )
        )
    return results


async def admin_stats_latency_percentiles(
    hours: int = Query(default=24, ge=1, le=8760),
    bucket_minutes: int = Query(default=60, ge=1, le=10080),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[StatsLatencyPercentileBucketOut]:
    start_time, end_time = _stats_time_window(hours, since, until)
    rows = await _stats_rows(session, start_time, end_time)
    buckets = _bucket_keys(start_time, end_time, bucket_minutes)
    bucket_seconds = bucket_minutes * 60
    for log, *_ in rows:
        bucket = buckets.get(_floor_bucket(_normalize_datetime(log.created_at), bucket_seconds))
        if bucket is None:
            continue
        latencies = bucket["latencies"]
        if isinstance(latencies, list):
            latencies.append(log.latency_ms)
    return [
        StatsLatencyPercentileBucketOut(
            bucket_start=bucket_start,
            p50_ms=_percentile(data["latencies"], 0.50)
            if isinstance(data["latencies"], list)
            else None,
            p95_ms=_percentile(data["latencies"], 0.95)
            if isinstance(data["latencies"], list)
            else None,
            p99_ms=_percentile(data["latencies"], 0.99)
            if isinstance(data["latencies"], list)
            else None,
        )
        for bucket_start, data in sorted(buckets.items())
    ]


def _distribution_items(
    totals: dict[str, dict[str, int]],
    token_basis: bool,
    limit: int,
) -> list[StatsDistributionItemOut]:
    denominator = sum(
        item["total_tokens"] if token_basis else item["request_count"]
        for item in totals.values()
    )
    rows = [
        StatsDistributionItemOut(
            name=name,
            request_count=data["request_count"],
            total_tokens=data["total_tokens"],
            percent=(
                (data["total_tokens"] if token_basis else data["request_count"])
                / denominator
                * 100
            )
            if denominator
            else 0.0,
        )
        for name, data in totals.items()
    ]
    rows.sort(
        key=lambda item: item.total_tokens if token_basis else item.request_count,
        reverse=True,
    )
    return rows[:limit]


async def admin_stats_distribution_models(
    hours: int = Query(default=24, ge=1, le=8760),
    limit: int = Query(default=12, ge=1, le=100),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[StatsDistributionItemOut]:
    start_time, end_time = _stats_time_window(hours, since, until)
    rows = await _stats_rows(session, start_time, end_time)
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"request_count": 0, "total_tokens": 0}
    )
    for log, *_ in rows:
        key = log.model_alias or "unknown"
        totals[key]["request_count"] += 1
        totals[key]["total_tokens"] += _log_total_tokens(log)
    return _distribution_items(totals, token_basis=True, limit=limit)


async def admin_stats_distribution_groups(
    hours: int = Query(default=24, ge=1, le=8760),
    limit: int = Query(default=12, ge=1, le=100),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[StatsDistributionItemOut]:
    start_time, end_time = _stats_time_window(hours, since, until)
    rows = await _stats_rows(session, start_time, end_time)
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"request_count": 0, "total_tokens": 0}
    )
    for log, *_ in rows:
        key = log.rule_group or "default"
        totals[key]["request_count"] += 1
        totals[key]["total_tokens"] += _log_total_tokens(log)
    return _distribution_items(totals, token_basis=False, limit=limit)


async def admin_stats_top_keys(
    hours: int = Query(default=24, ge=1, le=8760),
    limit: int = Query(default=10, ge=1, le=100),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[StatsTopKeyOut]:
    start_time, end_time = _stats_time_window(hours, since, until)
    rows = await _stats_rows(session, start_time, end_time)
    totals: dict[int, dict[str, object]] = {}
    for log, api_key, endpoint, dump in rows:
        key_id = log.api_key_id
        data = totals.setdefault(
            key_id,
            {
                "api_key_id": key_id,
                "endpoint_name": endpoint.name if endpoint else f"Endpoint {log.endpoint_id}",
                "key_preview": _mask_key(api_key.key) if api_key else f"key-{key_id}",
                "request_count": 0,
                "total_tokens": 0,
                "cache_hits": 0,
                "latencies": [],
            },
        )
        data["request_count"] = int(data["request_count"]) + 1
        data["total_tokens"] = int(data["total_tokens"]) + _log_total_tokens(log)
        if dump is not None and dump.is_cache_hit:
            data["cache_hits"] = int(data["cache_hits"]) + 1
        latencies = data["latencies"]
        if isinstance(latencies, list):
            latencies.append(log.latency_ms)

    ordered = sorted(
        totals.values(), key=lambda item: int(item["total_tokens"]), reverse=True
    )[:limit]
    results: list[StatsTopKeyOut] = []
    for data in ordered:
        request_count = int(data["request_count"])
        latencies = data["latencies"] if isinstance(data["latencies"], list) else []
        results.append(
            StatsTopKeyOut(
                api_key_id=int(data["api_key_id"]),
                endpoint_name=str(data["endpoint_name"]),
                key_preview=str(data["key_preview"]),
                request_count=request_count,
                total_tokens=int(data["total_tokens"]),
                cache_hit_rate=(
                    int(data["cache_hits"]) / request_count * 100
                    if request_count
                    else None
                ),
                avg_latency_ms=(
                    int(sum(latencies) / len(latencies)) if latencies else None
                ),
            )
        )
    return results


async def admin_dump_search(
    hours: int = Query(default=24, ge=1, le=8760),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    model: str | None = Query(default=None),
    rule_group: str | None = Query(default=None),
    status_code: int | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> DumpSearchOut:
    start_time, end_time = _stats_time_window(hours, since, until)
    filters = [
        DumpIndex.created_at >= start_time,
        DumpIndex.created_at <= end_time,
    ]
    if model:
        filters.append(DumpIndex.model_alias == model)
    if rule_group:
        filters.append(DumpIndex.rule_group == rule_group)
    if trace_id:
        filters.append(DumpIndex.trace_id.contains(trace_id))
    if status_code is not None:
        filters.append(RequestLog.status_code == status_code)

    base_stmt = (
        select(DumpIndex, RequestLog, Endpoint)
        .outerjoin(RequestLog, RequestLog.request_id == DumpIndex.request_id)
        .outerjoin(Endpoint, Endpoint.id == DumpIndex.endpoint_id)
        .where(*filters)
    )
    total_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = await session.scalar(total_stmt) or 0
    result = await session.execute(
        base_stmt.order_by(DumpIndex.created_at.desc()).offset(offset).limit(limit)
    )
    items: list[DumpSearchItemOut] = []
    for dump, log, endpoint in result.all():
        items.append(
            DumpSearchItemOut(
                request_id=dump.request_id,
                trace_id=dump.trace_id,
                model_alias=dump.model_alias,
                real_model=dump.real_model,
                endpoint_id=dump.endpoint_id,
                endpoint_name=endpoint.name if endpoint else None,
                api_key_id=log.api_key_id if log else None,
                rule_group=dump.rule_group,
                prompt_tokens=dump.prompt_tokens,
                completion_tokens=dump.completion_tokens,
                total_tokens=dump.total_tokens,
                cached_tokens=dump.cached_tokens,
                latency_ms=dump.latency_ms,
                is_stream=dump.is_stream,
                is_cache_hit=dump.is_cache_hit,
                stream_complete=dump.stream_complete,
                previous_interaction_id=dump.previous_interaction_id,
                status_code=log.status_code if log else None,
                file_path=dump.file_path,
                hostname=dump.hostname,
                created_at=dump.created_at,
            )
        )
    return DumpSearchOut(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
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
            execution_mode=candidate.execution_mode,
            agent_node=candidate.agent_name,
        )
        for index, candidate in enumerate(candidates)
    ]
    return RouteTestResponse(
        model=payload.model,
        rule_group=effective_group,
        candidates=ordered,
    )


async def _matching_route_rule(
    session: AsyncSession,
    model_alias: str,
    rule_group: str,
) -> tuple[RoutingRule | None, list[int], str]:
    result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.group_name == rule_group, RoutingRule.is_active.is_(True))
        .order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    for rule in result.scalars().all():
        if model_pattern_matches(rule.model_pattern, model_alias):
            target_key_ids, strategy = _deserialize_rule_config(rule.target_key_ids_json)
            return rule, target_key_ids, strategy
    return None, [], "weighted_round_robin"


async def _resolve_route_explain_policy(
    session: AsyncSession,
    model_alias: str,
    requested_group: str,
) -> tuple[str, bool, RoutingRule | None, list[int], str]:
    group = (requested_group or "default").strip() or "default"
    rule, target_key_ids, strategy = await _matching_route_rule(
        session, model_alias, group
    )
    if target_key_ids or group == "default":
        return group, False, rule, target_key_ids, strategy

    fallback_rule, fallback_targets, fallback_strategy = await _matching_route_rule(
        session, model_alias, "default"
    )
    return "default", True, fallback_rule, fallback_targets, fallback_strategy


def _key_in_rule_group(api_key: APIKey, group: str) -> bool:
    if hasattr(api_key, "in_rule_group"):
        return api_key.in_rule_group(group)
    return getattr(api_key, "rule_group", "default") == group


def _is_daily_limit_exhausted(api_key: APIKey) -> bool:
    if api_key.daily_limit is None:
        return False
    today = datetime.now(timezone.utc).date()
    used_today = 0 if api_key.used_today_date != today else (api_key.used_today or 0)
    return used_today >= api_key.daily_limit


async def route_explain(
    payload: RouteTestRequest, session: AsyncSession = Depends(get_session)
) -> RouteExplainResponse:
    redis = await get_redis()
    notifier = get_notifier()
    circuit_breaker = CircuitBreaker(redis, notifier=notifier)
    router_service = ModelRouter(circuit_breaker)
    effective_group, fallback_used, matched_rule, target_key_ids, strategy = (
        await _resolve_route_explain_policy(session, payload.model, payload.rule_group)
    )

    stmt = (
        select(APIKey, Endpoint, ModelMap)
        .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
        .join(ModelMap, ModelMap.endpoint_id == Endpoint.id)
        .where(ModelMap.model_alias == payload.model)
        .order_by(APIKey.id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    candidate_objects = [
        RouteCandidate(api_key=api_key, endpoint=endpoint, real_model=model_map.real_model)
        for api_key, endpoint, model_map in rows
    ]
    via_agent_names = {candidate.agent_name for candidate in candidate_objects if candidate.agent_name}
    agent_rows: dict[str, Agent] = {}
    if via_agent_names:
        agent_result = await session.execute(
            select(Agent).where(Agent.name.in_(via_agent_names))
        )
        agent_rows = {agent.name: agent for agent in agent_result.scalars().all()}
    agent_manager = get_agent_manager()

    available_candidates: list[RouteCandidate] = []
    excluded: list[RouteExplainExcludedOut] = []
    circuit_status_by_key: dict[int, object] = {}
    for candidate in candidate_objects:
        api_key = candidate.api_key
        endpoint = candidate.endpoint
        reasons: list[str] = []
        if target_key_ids:
            if api_key.id not in target_key_ids:
                reasons.append("api_key_not_in_rule_targets")
        elif not _key_in_rule_group(api_key, effective_group):
            reasons.append("api_key_not_in_rule_group")
        if not endpoint.is_active:
            reasons.append("endpoint_inactive")
        if not api_key.is_active:
            reasons.append("api_key_inactive")
        if _is_daily_limit_exhausted(api_key):
            reasons.append("daily_limit_exhausted")
        circuit_status = await circuit_breaker.get_status(api_key.id)
        circuit_status_by_key[api_key.id] = circuit_status
        if circuit_status.state == "open":
            reasons.append("circuit_open")
        if candidate.execution_mode == "via_agent":
            agent_name = candidate.agent_name
            agent = agent_rows.get(agent_name or "")
            if not agent_name:
                reasons.append("agent_missing")
            elif agent is None:
                reasons.append("agent_not_registered")
            elif not agent.is_active:
                reasons.append("agent_disabled")
            elif getattr(agent, "is_draining", False):
                reasons.append("agent_draining")
            elif agent_manager.get(agent_name) is None:
                reasons.append("agent_not_connected")

        if reasons:
            excluded.append(
                RouteExplainExcludedOut(
                    endpoint_id=endpoint.id,
                    endpoint_name=endpoint.name,
                    api_key_id=api_key.id,
                    real_model=candidate.real_model,
                    execution_mode=candidate.execution_mode,
                    agent_node=candidate.agent_name,
                    reasons=reasons,
                )
            )
            continue
        available_candidates.append(candidate)

    ordered = await router_service.order_candidates(
        available_candidates,
        strategy,
        model_alias=payload.model,
        effective_group=effective_group,
        target_key_ids=target_key_ids,
    )
    sticky_api_key_id = (
        await router_service.get_sequential_active_key_id(
            model_alias=payload.model,
            effective_group=effective_group,
            target_key_ids=target_key_ids,
        )
        if strategy == "sequential"
        else None
    )
    candidates = [
        RouteExplainCandidateOut(
            order=index + 1,
            endpoint_id=candidate.endpoint.id,
            endpoint_name=candidate.endpoint.name,
            api_key_id=candidate.api_key.id,
            weight=candidate.api_key.weight,
            real_model=candidate.real_model,
            execution_mode=candidate.execution_mode,
            agent_node=candidate.agent_name,
            circuit_state=circuit_status_by_key[candidate.api_key.id].state,
            circuit_failures=circuit_status_by_key[candidate.api_key.id].failures,
            circuit_ttl_seconds=circuit_status_by_key[
                candidate.api_key.id
            ].ttl_seconds,
            sticky_active=candidate.api_key.id == sticky_api_key_id,
            selected=index == 0,
        )
        for index, candidate in enumerate(ordered)
    ]

    notes: list[str] = []
    if not rows:
        notes.append("no_model_map_for_model_alias")
    if fallback_used:
        notes.append("fallback_to_default_rule_group")
    if matched_rule is None:
        notes.append("no_matching_active_rule")
    if not candidates:
        notes.append("no_available_candidates")

    return RouteExplainResponse(
        model=payload.model,
        requested_rule_group=payload.rule_group,
        effective_rule_group=effective_group,
        fallback_used=fallback_used,
        strategy=strategy,
        target_key_ids=target_key_ids,
        sticky_api_key_id=sticky_api_key_id,
        matched_rule_id=matched_rule.id if matched_rule else None,
        matched_rule_pattern=matched_rule.model_pattern if matched_rule else None,
        candidates=candidates,
        excluded=excluded,
        notes=notes,
    )

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_modules.admin_handlers import _deserialize_rule_config
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
    RouteExplainCandidateOut,
    RouteExplainExcludedOut,
    RouteExplainResponse,
    RouteTestRequest,
    RouteTestResponse,
    UsageGroupStat,
    UsageStatsOut,
    UsageTopKey,
)
from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.models import APIKey, Agent, Endpoint, ModelMap, RequestLog, RoutingRule
from app.db.session import get_session
from app.services.agent_transport import get_agent_manager
from app.services.agents import build_agent_statuses, list_agents
from app.services.circuit_breaker import CircuitBreaker
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
        try:
            import re

            if re.match(rule.model_pattern, model_alias):
                target_key_ids, strategy = _deserialize_rule_config(
                    rule.target_key_ids_json
                )
                return rule, target_key_ids, strategy
        except re.error:
            continue
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
        if not await circuit_breaker.is_available(api_key.id):
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

    ordered = ModelRouter._order_candidates(
        available_candidates,
        strategy,
        f"{payload.model}:{effective_group}:{strategy}",
        target_key_ids,
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
        matched_rule_id=matched_rule.id if matched_rule else None,
        matched_rule_pattern=matched_rule.model_pattern if matched_rule else None,
        candidates=candidates,
        excluded=excluded,
        notes=notes,
    )

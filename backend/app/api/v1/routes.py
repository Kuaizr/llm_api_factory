from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import asyncio
import json
import re
import shlex
import time
import uuid
from typing import AsyncGenerator, Iterable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.http_client import get_http_client
from app.core.redis import get_redis
from app.db.models import APIKey, Agent, Endpoint, ModelMap, RequestLog, RoutingRule
from app.db.session import SessionLocal, get_session
from app.services.agent_transport import AgentRequest, AgentUnavailableError, get_agent_manager
from app.services.agents import (
    build_agent_statuses,
    get_agent_by_name,
    hash_agent_token,
    issue_agent_token,
    list_agents,
    upsert_agent,
    verify_agent_token,
)
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import HealthProbeResult, HealthProbeStore
from app.services.notifications import ALERT_EVENTS, AlertPolicyStore, get_notifier
from app.services.router import ModelRouter, RouteCandidate

router = APIRouter()

AGENT_INSTALL_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "scripts" / "agent_install.sh"
)

RETRYABLE_STATUSES = {401, 429, 500, 502, 503, 504}
CIRCUIT_BREAKER_STATUSES = {401, 429}


class EndpointCreate(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    provider: str = "openai"
    strategy: str = "weighted_round_robin"
    agent_node: str | None = None
    is_active: bool = True


class EndpointUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_header_name: str | None = None
    auth_header_prefix: str | None = None
    provider: str | None = None
    strategy: str | None = None
    agent_node: str | None = None
    is_active: bool | None = None


class EndpointOut(BaseModel):
    id: int
    name: str
    base_url: str
    auth_header_name: str
    auth_header_prefix: str
    provider: str
    strategy: str
    agent_node: str | None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class APIKeyCreate(BaseModel):
    endpoint_id: int
    key: str = Field(..., min_length=1)
    name: str | None = None
    rule_group: str = "default"
    weight: int = 1
    rpm_limit: int | None = None
    daily_limit: int | None = None
    used_today: int = 0
    total_usage: int = 0
    is_active: bool = True


class EndpointKeyCreate(BaseModel):
    key: str = Field(..., min_length=1)
    name: str | None = None
    rule_group: str = "default"
    weight: int = 1
    rpm_limit: int | None = None
    daily_limit: int | None = None
    used_today: int = 0
    total_usage: int = 0
    is_active: bool = True


class APIKeyUpdate(BaseModel):
    key: str | None = None
    name: str | None = None
    rule_group: str | None = None
    weight: int | None = None
    rpm_limit: int | None = None
    daily_limit: int | None = None
    used_today: int | None = None
    total_usage: int | None = None
    is_active: bool | None = None


class APIKeyOut(BaseModel):
    id: int
    endpoint_id: int
    key: str
    name: str | None
    rule_group: str
    weight: int
    rpm_limit: int | None
    daily_limit: int | None
    used_today: int
    total_usage: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EndpointKeyOut(BaseModel):
    id: int
    key_preview: str
    name: str | None
    rpm_limit: int | None
    daily_limit: int | None
    used_today: int
    is_active: bool


class EndpointDetailOut(BaseModel):
    id: int
    name: str
    base_url: str
    provider: str
    strategy: str
    is_active: bool
    status: str
    latency: int
    uptime: float
    is_agent_enabled: bool
    agent_node: str | None
    model_count: int
    keys: list[EndpointKeyOut]


class RoutingRuleCreate(BaseModel):
    model_pattern: str
    group_name: str = "default"
    priority: int = 10
    strategy: str = "weighted_round_robin"
    is_active: bool = True
    target_key_ids: list[int]


class RoutingRuleUpdate(BaseModel):
    model_pattern: str | None = None
    group_name: str | None = None
    priority: int | None = None
    strategy: str | None = None
    is_active: bool | None = None
    target_key_ids: list[int] | None = None


class RoutingRuleOut(BaseModel):
    id: int
    model_pattern: str
    group_name: str
    priority: int
    strategy: str
    is_active: bool
    target_key_ids: list[int]
    request_count: int = 0
    total_tokens: int = 0
    avg_ttft_ms: int | None = None
    avg_tps: float | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthLoginRequest(BaseModel):
    password: str


class AuthLoginResponse(BaseModel):
    token: str
    role: str
    issued_at: datetime


class AuthMeResponse(BaseModel):
    role: str
    is_admin: bool


class UsageGroupStat(BaseModel):
    group_name: str
    percent: float
    total_tokens: int


class UsageTopKey(BaseModel):
    api_key_id: int
    endpoint_name: str
    key_preview: str
    total_tokens: int


class UsageStatsOut(BaseModel):
    groups: list[UsageGroupStat]
    top_keys: list[UsageTopKey]
    total_tokens_today: int
    generated_at: datetime


class DashboardEndpointOut(BaseModel):
    id: int
    name: str
    base_url: str
    provider: str
    status: str
    latency: int
    uptime: float
    agent_node: str | None


class DashboardAgentOut(BaseModel):
    id: int
    name: str
    region: str | None
    status: str
    last_seen_at: datetime | None
    endpoint_url: str | None


class DashboardStatusOut(BaseModel):
    endpoints: list[DashboardEndpointOut]
    agents: list[DashboardAgentOut]
    generated_at: datetime


class ModelMapCreate(BaseModel):
    endpoint_id: int
    model_alias: str = Field(..., min_length=1)
    real_model: str = Field(..., min_length=1)


class ModelMapUpdate(BaseModel):
    model_alias: str | None = None
    real_model: str | None = None


class ModelMapOut(BaseModel):
    id: int
    endpoint_id: int
    model_alias: str
    real_model: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RequestLogOut(BaseModel):
    id: int
    request_id: str
    trace_id: str
    model_alias: str
    endpoint_id: int
    api_key_id: int
    rule_group: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    ttft_ms: int | None
    tps: float | None
    status_code: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OverviewOut(BaseModel):
    endpoints: int
    api_keys: int
    model_maps: int
    request_logs: int
    generated_at: datetime


class HealthStatusOut(BaseModel):
    api_key_id: int
    endpoint_id: int
    endpoint_name: str
    rule_group: str
    is_active: bool
    probe_status: str
    probe_status_code: int | None
    probe_latency_ms: int | None
    probe_checked_at: datetime | None
    probe_real_model: str | None
    circuit_state: str
    circuit_failures: int
    circuit_ttl_seconds: int | None


class MetricsBucketOut(BaseModel):
    bucket_start: datetime
    request_count: int
    rps: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    avg_latency_ms: int | None


class HealthProbeBucketOut(BaseModel):
    bucket_start: datetime
    success_count: int
    failure_count: int
    error_count: int
    avg_latency_ms: int | None


class AlertPolicyOut(BaseModel):
    event: str
    enabled: bool
    silence_until: datetime | None
    threshold_ms: int | None


class AlertPolicyUpdate(BaseModel):
    enabled: bool | None = None
    silence_minutes: int | None = Field(default=None, ge=0, le=10080)
    silence_until: datetime | None = None
    threshold_ms: int | None = Field(default=None, ge=0, le=120000)


class AgentBootstrapRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class AgentBootstrapOut(BaseModel):
    agent_id: int
    name: str
    token: str
    install_command: str


class AgentHeartbeatRequest(BaseModel):
    name: str = Field(..., min_length=1)
    region: str | None = None
    endpoint_url: str | None = None
    token: str | None = None
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = Field(default=None, ge=0, le=120000)


class AgentStatusOut(BaseModel):
    id: int
    name: str
    region: str | None
    endpoint_url: str | None
    supports_gpt: bool | None = None
    supports_gemini: bool | None = None
    supports_claude: bool | None = None
    probe_latency_ms: int | None = None
    probe_checked_at: datetime | None = None
    is_active: bool
    last_seen_at: datetime | None
    status: str


class DeleteResponse(BaseModel):
    status: str = "ok"


class RouteTestRequest(BaseModel):
    model: str = Field(..., min_length=1)
    rule_group: str = "default"


class RouteCandidateOut(BaseModel):
    order: int
    endpoint_id: int
    endpoint_name: str
    api_key_id: int
    weight: int
    real_model: str


class RouteTestResponse(BaseModel):
    model: str
    rule_group: str
    candidates: list[RouteCandidateOut]


def _require_master_auth(request: Request) -> None:
    settings = get_settings()
    if not settings.master_auth_token:
        return
    expected = f"Bearer {settings.master_auth_token}"
    if request.headers.get("Authorization") != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _is_master_authorized(request: Request) -> bool:
    settings = get_settings()
    if not settings.master_auth_token:
        return True
    expected = f"Bearer {settings.master_auth_token}"
    return request.headers.get("Authorization") == expected


def _require_agent_auth(request: Request) -> None:
    settings = get_settings()
    if not settings.agent_auth_token:
        return
    expected = f"Bearer {settings.agent_auth_token}"
    if request.headers.get("Authorization") != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = f"{cleaned[:-1]}+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime format") from exc


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _today_utc_date() -> date:
    return datetime.now(timezone.utc).date()


def _normalize_api_key_usage(api_key: APIKey, today: date) -> bool:
    if api_key.used_today_date != today:
        api_key.used_today = 0
        api_key.used_today_date = today
        return True
    return False


def _parse_bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _build_agent_install_command(
    script_url: str,
    ws_url: str,
    heartbeat_url: str,
    name: str,
    token: str,
    region: str | None,
    endpoint_url: str | None,
    repo_url: str | None,
) -> str:
    args = [
        "--ws-url",
        ws_url,
        "--heartbeat-url",
        heartbeat_url,
        "--agent-name",
        name,
        "--agent-token",
        token,
    ]
    if repo_url:
        args.extend(["--repo", repo_url])
    if region:
        args.extend(["--agent-region", region])
    if endpoint_url:
        args.extend(["--agent-endpoint-url", endpoint_url])
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    return f"curl -fsSL {shlex.quote(script_url)} | bash -s -- {quoted_args}"


async def _authorize_agent_token(
    session: AsyncSession,
    name: str,
    token: str | None,
    header_value: str | None,
) -> Agent | None:
    settings = get_settings()
    header_token = _parse_bearer_token(header_value)
    agent = await get_agent_by_name(session, name)

    if settings.agent_auth_token and header_token == settings.agent_auth_token:
        return agent

    if agent and agent.auth_token_hash and token:
        if verify_agent_token(token, agent.auth_token_hash):
            return agent

    if not settings.agent_auth_token and (agent is None or agent.auth_token_hash is None):
        return agent

    raise HTTPException(status_code=401, detail="Unauthorized")


def _mask_key(value: str) -> str:
    trimmed = str(value or "")
    if len(trimmed) <= 6:
        return trimmed
    return f"{trimmed[:3]}...{trimmed[-4:]}"


def _build_endpoint_detail(
    endpoint: Endpoint,
    status: str,
    latency_ms: int,
    uptime: float,
) -> EndpointDetailOut:
    keys = [
        EndpointKeyOut(
            id=key.id,
            key_preview=_mask_key(key.key),
            name=key.name,
            rpm_limit=key.rpm_limit,
            daily_limit=key.daily_limit,
            used_today=key.used_today,
            is_active=key.is_active,
        )
        for key in (endpoint.api_keys or [])
    ]
    model_count = len(endpoint.model_maps or [])
    return EndpointDetailOut(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        provider=endpoint.provider,
        strategy=endpoint.strategy,
        is_active=endpoint.is_active,
        status=status,
        latency=latency_ms,
        uptime=uptime,
        is_agent_enabled=bool(endpoint.agent_node),
        agent_node=endpoint.agent_node,
        model_count=model_count,
        keys=keys,
    )


def _resolve_endpoint_status(
    endpoint: Endpoint, probe_results: dict[int, HealthProbeResult]
) -> str:
    if not endpoint.is_active:
        return "offline"
    has_probe = False
    for key in endpoint.api_keys or []:
        probe = probe_results.get(key.id)
        if probe is None:
            continue
        has_probe = True
        if probe.status == "success":
            return "online"
    if has_probe:
        return "offline"
    return "online"


def _build_dashboard_endpoint(endpoint: Endpoint) -> DashboardEndpointOut:
    status = "online" if endpoint.is_active else "offline"
    latency = 0
    uptime = 100.0 if endpoint.is_active else 0.0
    return DashboardEndpointOut(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        provider=endpoint.provider,
        status=status,
        latency=latency,
        uptime=uptime,
        agent_node=endpoint.agent_node,
    )


DEFAULT_RULE_STRATEGY = "weighted_round_robin"


def _parse_target_key_ids(data: object) -> list[int]:
    if not isinstance(data, list):
        return []
    parsed: list[int] = []
    for item in data:
        if isinstance(item, int):
            parsed.append(item)
        elif isinstance(item, str) and item.isdigit():
            parsed.append(int(item))
    return parsed


def _serialize_rule_config(target_key_ids: list[int], strategy: str) -> str:
    return json.dumps({
        "target_key_ids": target_key_ids,
        "strategy": strategy,
    })


def _deserialize_rule_config(raw: str) -> tuple[list[int], str]:
    if not raw:
        return [], DEFAULT_RULE_STRATEGY
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], DEFAULT_RULE_STRATEGY
    if isinstance(data, list):
        return _parse_target_key_ids(data), DEFAULT_RULE_STRATEGY
    if isinstance(data, dict):
        target_key_ids = _parse_target_key_ids(data.get("target_key_ids", []))
        strategy = data.get("strategy") or DEFAULT_RULE_STRATEGY
        if not isinstance(strategy, str):
            strategy = str(strategy)
        return target_key_ids, strategy
    return [], DEFAULT_RULE_STRATEGY


def _normalize_rule_group_name(value: str) -> str:
    return str(value or "").strip()


async def _ensure_rule_group_available(
    session: AsyncSession, group_name: str, exclude_rule_id: int | None = None
) -> str:
    normalized = _normalize_rule_group_name(group_name)
    if not normalized or normalized.lower() == "default":
        raise HTTPException(status_code=400, detail="Invalid rule group name")
    stmt = select(RoutingRule.id).where(RoutingRule.group_name == normalized)
    if exclude_rule_id is not None:
        stmt = stmt.where(RoutingRule.id != exclude_rule_id)
    exists = await session.scalar(stmt)
    if exists is not None:
        raise HTTPException(status_code=400, detail="Rule group already exists")
    return normalized


def _floor_bucket(value: datetime, bucket_seconds: int) -> datetime:
    epoch = int(value.timestamp())
    bucket_epoch = epoch - (epoch % bucket_seconds)
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)


def build_metric_buckets(
    logs: Iterable[RequestLog],
    start_time: datetime,
    end_time: datetime,
    bucket_minutes: int,
) -> list[MetricsBucketOut]:
    if bucket_minutes <= 0:
        return []
    bucket_seconds = bucket_minutes * 60
    start_utc = _normalize_datetime(start_time)
    end_utc = _normalize_datetime(end_time)
    if end_utc < start_utc:
        return []

    bucket_start = _floor_bucket(start_utc, bucket_seconds)
    buckets: dict[datetime, dict[str, int]] = {}
    while bucket_start <= end_utc:
        buckets[bucket_start] = {
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_sum": 0,
        }
        bucket_start += timedelta(seconds=bucket_seconds)

    for log in logs:
        created_at = _normalize_datetime(log.created_at)
        if created_at < start_utc or created_at > end_utc:
            continue
        bucket_key = _floor_bucket(created_at, bucket_seconds)
        bucket = buckets.get(bucket_key)
        if bucket is None:
            continue
        bucket["request_count"] += 1
        prompt_tokens = log.prompt_tokens or 0
        completion_tokens = log.completion_tokens or 0
        total_tokens = (
            log.total_tokens
            if log.total_tokens is not None
            else prompt_tokens + completion_tokens
        )
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["total_tokens"] += total_tokens
        bucket["latency_sum"] += log.latency_ms

    results: list[MetricsBucketOut] = []
    for bucket_start, data in sorted(buckets.items()):
        count = data["request_count"]
        avg_latency = int(data["latency_sum"] / count) if count else None
        results.append(
            MetricsBucketOut(
                bucket_start=bucket_start,
                request_count=count,
                rps=count / bucket_seconds,
                prompt_tokens=data["prompt_tokens"],
                completion_tokens=data["completion_tokens"],
                total_tokens=data["total_tokens"],
                avg_latency_ms=avg_latency,
            )
        )
    return results


def build_health_probe_buckets(
    results: Iterable[HealthProbeResult],
    start_time: datetime,
    end_time: datetime,
    bucket_minutes: int,
) -> list[HealthProbeBucketOut]:
    if bucket_minutes <= 0:
        return []
    bucket_seconds = bucket_minutes * 60
    start_utc = _normalize_datetime(start_time)
    end_utc = _normalize_datetime(end_time)
    if end_utc < start_utc:
        return []

    bucket_start = _floor_bucket(start_utc, bucket_seconds)
    buckets: dict[datetime, dict[str, int]] = {}
    while bucket_start <= end_utc:
        buckets[bucket_start] = {
            "success_count": 0,
            "failure_count": 0,
            "error_count": 0,
            "latency_sum": 0,
            "latency_count": 0,
        }
        bucket_start += timedelta(seconds=bucket_seconds)

    for result in results:
        checked_at = _normalize_datetime(result.checked_at)
        if checked_at < start_utc or checked_at > end_utc:
            continue
        bucket_key = _floor_bucket(checked_at, bucket_seconds)
        bucket = buckets.get(bucket_key)
        if bucket is None:
            continue
        if result.status == "success":
            bucket["success_count"] += 1
        elif result.status == "failure":
            bucket["failure_count"] += 1
        else:
            bucket["error_count"] += 1
        if result.latency_ms is not None:
            bucket["latency_sum"] += result.latency_ms
            bucket["latency_count"] += 1

    results_out: list[HealthProbeBucketOut] = []
    for bucket_start, data in sorted(buckets.items()):
        count = data["latency_count"]
        avg_latency = int(data["latency_sum"] / count) if count else None
        results_out.append(
            HealthProbeBucketOut(
                bucket_start=bucket_start,
                success_count=data["success_count"],
                failure_count=data["failure_count"],
                error_count=data["error_count"],
                avg_latency_ms=avg_latency,
            )
        )
    return results_out


@router.get("/v1/models")
async def list_models(session: AsyncSession = Depends(get_session)) -> dict:
    result = await session.execute(select(ModelMap.model_alias).distinct())
    models = [
        {"id": model_alias, "object": "model", "owned_by": "proxy"}
        for (model_alias,) in result.all()
    ]
    return {"object": "list", "data": models}


@router.post("/auth/login", response_model=AuthLoginResponse)
async def auth_login(payload: AuthLoginRequest) -> AuthLoginResponse:
    settings = get_settings()
    if not settings.master_auth_token or payload.password != settings.master_auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return AuthLoginResponse(
        token=settings.master_auth_token,
        role="admin",
        issued_at=datetime.now(timezone.utc),
    )


@router.get("/auth/me", response_model=AuthMeResponse)
async def auth_me(request: Request) -> AuthMeResponse:
    if not _is_master_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return AuthMeResponse(role="admin", is_admin=True)


@router.get("/v1/status/dashboard", response_model=DashboardStatusOut)
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


@router.get(
    "/admin/overview",
    response_model=OverviewOut,
    dependencies=[Depends(_require_master_auth)],
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
        generated_at=datetime.utcnow(),
    )


@router.get("/agent/install.sh")
async def agent_install_script() -> Response:
    if not AGENT_INSTALL_SCRIPT_PATH.exists():
        raise HTTPException(status_code=404, detail="Agent install script missing")
    return Response(
        AGENT_INSTALL_SCRIPT_PATH.read_text(encoding="utf-8"),
        media_type="text/plain",
    )


@router.post(
    "/admin/agents/bootstrap",
    response_model=AgentBootstrapOut,
    dependencies=[Depends(_require_master_auth)],
)
async def admin_agent_bootstrap(
    payload: AgentBootstrapRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentBootstrapOut:
    from app.services.agents import get_agent_by_name

    # Check if agent name already exists
    existing_agent = await get_agent_by_name(session, payload.name)
    if existing_agent is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Agent 名称 '{payload.name}' 已存在，请使用其他名称",
        )

    settings = get_settings()
    base = str(request.base_url).rstrip("/")
    script_url = settings.agent_install_script_url or f"{base}/agent/install.sh"
    repo_url = settings.agent_install_repo_url
    token = issue_agent_token()
    token_hash = hash_agent_token(token)
    agent = await upsert_agent(
        session,
        name=payload.name,
        region=None,
        endpoint_url=None,
        auth_token_hash=token_hash,
        touch=False,
    )
    ws_url = f"{base}/agent/ws"
    heartbeat_url = f"{base}/agent/heartbeat"
    install_command = _build_agent_install_command(
        script_url=script_url,
        ws_url=ws_url,
        heartbeat_url=heartbeat_url,
        name=agent.name,
        token=token,
        region=None,
        endpoint_url=None,
        repo_url=repo_url,
    )
    return AgentBootstrapOut(
        agent_id=agent.id,
        name=agent.name,
        token=token,
        install_command=install_command,
    )


@router.websocket("/agent/ws")
async def agent_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    manager = get_agent_manager()
    connection = None
    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "register":
                name = str(message.get("name") or "").strip()
                if not name:
                    await websocket.send_json({"type": "error", "error": "missing name"})
                    continue
                raw_token = message.get("token")
                token = raw_token if isinstance(raw_token, str) else None
                region = message.get("region")
                if not isinstance(region, str) or not region.strip():
                    region = None
                endpoint_url = message.get("endpoint_url")
                if not isinstance(endpoint_url, str) or not endpoint_url.strip():
                    endpoint_url = None
                supports_gpt = message.get("supports_gpt")
                if not isinstance(supports_gpt, bool):
                    supports_gpt = None
                supports_gemini = message.get("supports_gemini")
                if not isinstance(supports_gemini, bool):
                    supports_gemini = None
                supports_claude = message.get("supports_claude")
                if not isinstance(supports_claude, bool):
                    supports_claude = None
                probe_latency_ms = message.get("probe_latency_ms")
                if not isinstance(probe_latency_ms, int):
                    probe_latency_ms = None

                async with SessionLocal() as session:
                    try:
                        await _authorize_agent_token(
                            session,
                            name=name,
                            token=token,
                            header_value=websocket.headers.get("Authorization"),
                        )
                    except HTTPException:
                        await websocket.send_json(
                            {"type": "error", "error": "unauthorized"}
                        )
                        await websocket.close(code=1008)
                        return
                    await upsert_agent(
                        session,
                        name=name,
                        region=region,
                        endpoint_url=endpoint_url,
                        supports_gpt=supports_gpt,
                        supports_gemini=supports_gemini,
                        supports_claude=supports_claude,
                        probe_latency_ms=probe_latency_ms,
                    )
                connection = manager.register(name, websocket)
                await websocket.send_json({"type": "registered", "name": name})
                continue

            if connection is None:
                await websocket.send_json({"type": "error", "error": "not registered"})
                continue

            if message_type == "heartbeat":
                connection.touch()
                continue

            await manager.handle_message(connection.name, message)
    except WebSocketDisconnect:
        if connection:
            manager.unregister(connection.name)


@router.post(
    "/agent/heartbeat",
    response_model=AgentStatusOut,
)
async def agent_heartbeat(
    payload: AgentHeartbeatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentStatusOut:
    await _authorize_agent_token(
        session,
        name=payload.name,
        token=payload.token,
        header_value=request.headers.get("Authorization"),
    )
    now = datetime.now(timezone.utc)
    agent = await upsert_agent(
        session,
        name=payload.name,
        region=payload.region,
        endpoint_url=payload.endpoint_url,
        now=now,
        supports_gpt=payload.supports_gpt,
        supports_gemini=payload.supports_gemini,
        supports_claude=payload.supports_claude,
        probe_latency_ms=payload.probe_latency_ms,
    )
    settings = get_settings()
    status = build_agent_statuses(
        [agent], now, settings.agent_heartbeat_timeout_seconds
    )[0]
    return AgentStatusOut(
        id=status.id,
        name=status.name,
        region=status.region,
        endpoint_url=status.endpoint_url,
        supports_gpt=status.supports_gpt,
        supports_gemini=status.supports_gemini,
        supports_claude=status.supports_claude,
        probe_latency_ms=status.probe_latency_ms,
        probe_checked_at=status.probe_checked_at,
        is_active=status.is_active,
        last_seen_at=status.last_seen_at,
        status=status.status,
    )


@router.get(
    "/admin/agents",
    response_model=list[AgentStatusOut],
    dependencies=[Depends(_require_master_auth)],
)
async def admin_agents(
    session: AsyncSession = Depends(get_session),
) -> list[AgentStatusOut]:
    settings = get_settings()
    agents = await list_agents(session)
    statuses = build_agent_statuses(
        agents, datetime.now(timezone.utc), settings.agent_heartbeat_timeout_seconds
    )
    return [
        AgentStatusOut(
            id=status.id,
            name=status.name,
            region=status.region,
            endpoint_url=status.endpoint_url,
            supports_gpt=status.supports_gpt,
            supports_gemini=status.supports_gemini,
            supports_claude=status.supports_claude,
            probe_latency_ms=status.probe_latency_ms,
            probe_checked_at=status.probe_checked_at,
            is_active=status.is_active,
            last_seen_at=status.last_seen_at,
            status=status.status,
        )
        for status in statuses
    ]


@router.delete(
    "/admin/agents/{agent_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(_require_master_auth)],
)
async def admin_delete_agent(
    agent_id: int,
    session: AsyncSession = Depends(get_session),
) -> DeleteResponse:
    from app.db.models import Agent
    stmt = delete(Agent).where(Agent.id == agent_id)
    await session.execute(stmt)
    await session.commit()
    return DeleteResponse()


@router.post(
    "/admin/agents/{agent_id}/rotate-token",
    response_model=AgentBootstrapOut,
    dependencies=[Depends(_require_master_auth)],
)
async def admin_rotate_agent_token(
    agent_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentBootstrapOut:
    """
    重新生成Agent Token。
    注意：只有未部署的Agent才能重新生成Token。
    已部署的Agent重新生成Token会导致原有的Agent无法连接。
    """
    from sqlalchemy import select
    from app.db.models import Agent

    stmt = select(Agent).where(Agent.id == agent_id)
    result = await session.execute(stmt)
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # 检查Agent是否已部署（auth_token_hash 非空表示已部署）
    if agent.auth_token_hash and agent.auth_token_hash.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent.name}' 已部署，无法重新生成Token。如需重新部署，请先删除该Agent并创建新的。",
        )

    # 未部署，可以重新生成Token
    token = issue_agent_token()
    token_hash = hash_agent_token(token)
    agent.auth_token_hash = token_hash
    await session.commit()
    base = str(request.base_url).rstrip("/")
    settings = get_settings()
    script_url = settings.agent_install_script_url or f"{base}/agent/install.sh"
    repo_url = settings.agent_install_repo_url
    ws_url = f"{base}/agent/ws"
    heartbeat_url = f"{base}/agent/heartbeat"
    install_command = _build_agent_install_command(
        script_url=script_url,
        ws_url=ws_url,
        heartbeat_url=heartbeat_url,
        name=agent.name,
        token=token,
        region=agent.region,
        endpoint_url=agent.endpoint_url,
        repo_url=repo_url,
    )
    return AgentBootstrapOut(
        agent_id=agent.id,
        name=agent.name,
        token=token,
        install_command=install_command,
    )


@router.get(
    "/admin/alerts",
    response_model=list[AlertPolicyOut],
    dependencies=[Depends(_require_master_auth)],
)
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


@router.put(
    "/admin/alerts/{event}",
    response_model=AlertPolicyOut,
    dependencies=[Depends(_require_master_auth)],
)
async def admin_alert_policy_update(event: str, payload: AlertPolicyUpdate) -> AlertPolicyOut:
    if event not in ALERT_EVENTS:
        raise HTTPException(status_code=404, detail="Unknown alert event")
    redis = await get_redis()
    store = AlertPolicyStore(redis)
    current = await store.get_policy(event)

    enabled = payload.enabled if payload.enabled is not None else current.enabled
    silence_until = payload.silence_until
    if payload.silence_minutes is not None:
        silence_until = (
            datetime.utcnow() + timedelta(minutes=payload.silence_minutes)
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
    return AlertPolicyOut(
        event=policy.event,
        enabled=policy.enabled,
        silence_until=policy.silence_until,
        threshold_ms=policy.threshold_ms,
    )


@router.get(
    "/admin/stats/usage",
    response_model=UsageStatsOut,
    dependencies=[Depends(_require_master_auth)],
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
        group_name = log.rule_group or api_key.rule_group or "default"
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


@router.get(
    "/admin/metrics/timeseries",
    response_model=list[MetricsBucketOut],
    dependencies=[Depends(_require_master_auth)],
)
async def admin_metrics_timeseries(
    hours: int = Query(default=24, ge=1, le=8760),
    bucket_minutes: int = Query(default=60, ge=1, le=10080),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[MetricsBucketOut]:
    end_time = _parse_iso_datetime(until) or datetime.utcnow()
    start_time = _parse_iso_datetime(since) or end_time - timedelta(hours=hours)
    if start_time > end_time:
        raise HTTPException(status_code=400, detail="since must be before until")

    stmt = select(RequestLog).where(
        RequestLog.created_at >= start_time, RequestLog.created_at <= end_time
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()
    return build_metric_buckets(logs, start_time, end_time, bucket_minutes)


@router.get(
    "/admin/health-status/timeseries",
    response_model=list[HealthProbeBucketOut],
    dependencies=[Depends(_require_master_auth)],
)
async def admin_health_probe_timeseries(
    hours: int = Query(default=24, ge=1, le=168),
    bucket_minutes: int = Query(default=30, ge=1, le=1440),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[HealthProbeBucketOut]:
    end_time = _parse_iso_datetime(until) or datetime.utcnow()
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


@router.get(
    "/admin/health-status",
    response_model=list[HealthStatusOut],
    dependencies=[Depends(_require_master_auth)],
)
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
                rule_group=api_key.rule_group,
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


@router.post(
    "/admin/route-test",
    response_model=RouteTestResponse,
    dependencies=[Depends(_require_master_auth)],
)
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


@router.get(
    "/admin/endpoints",
    response_model=list[EndpointDetailOut],
    dependencies=[Depends(_require_master_auth)],
)
async def list_endpoints(session: AsyncSession = Depends(get_session)) -> list[EndpointDetailOut]:
    stmt = (
        select(Endpoint)
        .options(selectinload(Endpoint.api_keys), selectinload(Endpoint.model_maps))
        .order_by(Endpoint.id)
    )
    result = await session.execute(stmt)
    endpoints = result.scalars().unique().all()
    today = _today_utc_date()
    changed = False
    for endpoint in endpoints:
        for key in endpoint.api_keys or []:
            if _normalize_api_key_usage(key, today):
                changed = True
    if changed:
        await session.commit()

    redis = await get_redis()
    probe_store = HealthProbeStore(redis)
    api_key_ids = [
        key.id for endpoint in endpoints for key in (endpoint.api_keys or [])
    ]
    probe_results = await probe_store.read_many(api_key_ids)
    probe_series_map = await probe_store.read_series_many(api_key_ids)

    items: list[EndpointDetailOut] = []
    for endpoint in endpoints:
        series = [
            result
            for key in (endpoint.api_keys or [])
            for result in probe_series_map.get(key.id, [])
        ]
        success_latencies = [
            result.latency_ms
            for result in series
            if result.status == "success" and result.latency_ms is not None
        ]
        ping_latency = (
            int(sum(success_latencies) / len(success_latencies))
            if success_latencies
            else 0
        )
        total = len(series)
        success_count = sum(1 for result in series if result.status == "success")
        uptime = round(success_count / total * 100, 1) if total else 0.0
        items.append(
            _build_endpoint_detail(
                endpoint,
                _resolve_endpoint_status(endpoint, probe_results),
                ping_latency,
                uptime,
            )
        )
    return items


@router.post(
    "/admin/endpoints",
    response_model=EndpointOut,
    dependencies=[Depends(_require_master_auth)],
)
async def create_endpoint(
    payload: EndpointCreate, session: AsyncSession = Depends(get_session)
) -> EndpointOut:
    data = payload.model_dump()
    if data.get("agent_node") == "":
        data["agent_node"] = None
    endpoint = Endpoint(**data)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


@router.patch(
    "/admin/endpoints/{endpoint_id}",
    response_model=EndpointOut,
    dependencies=[Depends(_require_master_auth)],
)
async def update_endpoint(
    endpoint_id: int,
    payload: EndpointUpdate,
    session: AsyncSession = Depends(get_session),
) -> EndpointOut:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if data.get("agent_node") == "":
        data["agent_node"] = None
    for field, value in data.items():
        setattr(endpoint, field, value)
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


@router.post(
    "/admin/endpoints/{endpoint_id}/probe",
    response_model=list[ModelMapOut],
    dependencies=[Depends(_require_master_auth)],
)
async def probe_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[ModelMapOut]:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    api_key = await session.scalar(
        select(APIKey)
        .where(APIKey.endpoint_id == endpoint_id, APIKey.is_active.is_(True))
        .order_by(APIKey.id)
    )
    if not api_key:
        raise HTTPException(status_code=400, detail="No active API key for probe")

    settings = get_settings()
    redis = await get_redis()
    probe_store = HealthProbeStore(
        redis,
        ttl_seconds=settings.health_probe_result_ttl_seconds,
        series_ttl_seconds=settings.health_probe_series_ttl_seconds,
        series_max_entries=settings.health_probe_series_max_entries,
    )
    circuit_breaker = CircuitBreaker(redis, settings=settings)

    client = await get_http_client()
    url = f"{endpoint.base_url.rstrip('/')}/v1/models"
    header_name = endpoint.auth_header_name or "Authorization"
    header_prefix = endpoint.auth_header_prefix or "Bearer"
    headers = (
        {header_name: f"{header_prefix} {api_key.key}"}
        if header_prefix
        else {header_name: api_key.key}
    )

    status = "error"
    status_code: int | None = None
    latency_ms: int | None = None
    models: list[dict] = []
    response = None
    started_at = time.perf_counter()

    try:
        response = await client.get(
            url,
            headers=headers,
            timeout=settings.health_probe_timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        status_code = response.status_code
        if status_code >= 400:
            status = "failure"
        else:
            payload = response.json()
            payload_models = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(payload_models, list):
                status = "success"
                models = [item for item in payload_models if isinstance(item, dict)]
            else:
                status = "failure"
    except Exception:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        status = "error"
    finally:
        if response is not None:
            await response.aclose()

    if status == "success":
        await circuit_breaker.record_success(api_key.id)
    else:
        await circuit_breaker.record_failure(api_key.id)

    await probe_store.write(
        HealthProbeResult(
            api_key_id=api_key.id,
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
            real_model=None,
            status=status,
            status_code=status_code,
            latency_ms=latency_ms,
            checked_at=datetime.utcnow(),
        )
    )

    if status != "success":
        raise HTTPException(status_code=502, detail="Probe failed")

    existing = await session.execute(
        select(ModelMap).where(ModelMap.endpoint_id == endpoint_id)
    )
    existing_models = existing.scalars().all()
    existing_aliases = {model.model_alias for model in existing_models}
    existing_real_models = {model.real_model for model in existing_models}
    for item in models:
        model_id = item.get("id")
        if not model_id:
            continue
        alias = str(model_id)
        if alias in existing_aliases or alias in existing_real_models:
            continue
        session.add(
            ModelMap(endpoint_id=endpoint_id, model_alias=alias, real_model=alias)
        )
        existing_aliases.add(alias)
        existing_real_models.add(alias)
    await session.commit()

    result = await session.execute(
        select(ModelMap).where(ModelMap.endpoint_id == endpoint_id).order_by(ModelMap.id)
    )
    return result.scalars().all()


@router.delete(
    "/admin/endpoints/{endpoint_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(_require_master_auth)],
)
async def delete_endpoint(
    endpoint_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    await session.execute(delete(RequestLog).where(RequestLog.endpoint_id == endpoint_id))
    await session.execute(delete(ModelMap).where(ModelMap.endpoint_id == endpoint_id))
    await session.execute(delete(APIKey).where(APIKey.endpoint_id == endpoint_id))
    await session.delete(endpoint)
    await session.commit()
    return DeleteResponse()


@router.post(
    "/admin/endpoints/{endpoint_id}/keys",
    response_model=APIKeyOut,
    dependencies=[Depends(_require_master_auth)],
)
async def create_endpoint_key(
    endpoint_id: int,
    payload: EndpointKeyCreate,
    session: AsyncSession = Depends(get_session),
) -> APIKeyOut:
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    api_key = APIKey(endpoint_id=endpoint_id, **payload.model_dump())
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return api_key


@router.get(
    "/admin/api-keys",
    response_model=list[APIKeyOut],
    dependencies=[Depends(_require_master_auth)],
)
async def list_api_keys(
    endpoint_id: int | None = Query(default=None),
    rule_group: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[APIKeyOut]:
    stmt = select(APIKey).order_by(APIKey.id)
    if endpoint_id is not None:
        stmt = stmt.where(APIKey.endpoint_id == endpoint_id)
    if rule_group is not None:
        stmt = stmt.where(APIKey.rule_group == rule_group)
    result = await session.execute(stmt)
    api_keys = result.scalars().all()
    today = _today_utc_date()
    changed = False
    for api_key in api_keys:
        if _normalize_api_key_usage(api_key, today):
            changed = True
    if changed:
        await session.commit()
    return api_keys


@router.post(
    "/admin/api-keys",
    response_model=APIKeyOut,
    dependencies=[Depends(_require_master_auth)],
)
async def create_api_key(
    payload: APIKeyCreate, session: AsyncSession = Depends(get_session)
) -> APIKeyOut:
    api_key = APIKey(**payload.model_dump())
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return api_key


@router.put(
    "/admin/keys/{api_key_id}",
    response_model=APIKeyOut,
    dependencies=[Depends(_require_master_auth)],
)
async def update_key(
    api_key_id: int,
    payload: APIKeyUpdate,
    session: AsyncSession = Depends(get_session),
) -> APIKeyOut:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field, value in data.items():
        setattr(api_key, field, value)
    await session.commit()
    await session.refresh(api_key)
    return api_key


@router.patch(
    "/admin/api-keys/{api_key_id}",
    response_model=APIKeyOut,
    dependencies=[Depends(_require_master_auth)],
)
async def update_api_key(
    api_key_id: int,
    payload: APIKeyUpdate,
    session: AsyncSession = Depends(get_session),
) -> APIKeyOut:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field, value in data.items():
        setattr(api_key, field, value)
    await session.commit()
    await session.refresh(api_key)
    return api_key


@router.delete(
    "/admin/api-keys/{api_key_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(_require_master_auth)],
)
async def delete_api_key(
    api_key_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    await session.delete(api_key)
    await session.commit()
    return DeleteResponse()


@router.get(
    "/admin/rules",
    response_model=list[RoutingRuleOut],
    dependencies=[Depends(_require_master_auth)],
)
async def list_rules(session: AsyncSession = Depends(get_session)) -> list[RoutingRuleOut]:
    result = await session.execute(
        select(RoutingRule).order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    rules = result.scalars().all()
    log_result = await session.execute(
        select(RequestLog, APIKey).join(APIKey, RequestLog.api_key_id == APIKey.id)
    )
    log_rows = log_result.all()
    items: list[RoutingRuleOut] = []
    for rule in rules:
        target_key_ids, strategy = _deserialize_rule_config(rule.target_key_ids_json)
        try:
            matcher = re.compile(rule.model_pattern)
        except re.error:
            matcher = None
        request_count = 0
        total_tokens = 0
        ttft_sum = 0
        ttft_count = 0
        tps_sum = 0.0
        tps_count = 0
        if matcher:
            for log, api_key in log_rows:
                log_group = log.rule_group or api_key.rule_group or "default"
                if log_group != rule.group_name:
                    continue
                if not matcher.match(log.model_alias):
                    continue
                request_count += 1
                tokens = log.total_tokens
                if tokens is None:
                    tokens = (log.prompt_tokens or 0) + (log.completion_tokens or 0)
                total_tokens += tokens
                if log.ttft_ms is not None:
                    ttft_sum += log.ttft_ms
                    ttft_count += 1
                if log.tps is not None:
                    tps_sum += float(log.tps)
                    tps_count += 1
        avg_ttft_ms = int(ttft_sum / ttft_count) if ttft_count else None
        avg_tps = round(tps_sum / tps_count, 2) if tps_count else None
        items.append(
            RoutingRuleOut(
                id=rule.id,
                model_pattern=rule.model_pattern,
                group_name=rule.group_name,
                priority=rule.priority,
                strategy=strategy,
                is_active=rule.is_active,
                target_key_ids=target_key_ids,
                request_count=request_count,
                total_tokens=total_tokens,
                avg_ttft_ms=avg_ttft_ms,
                avg_tps=avg_tps,
                created_at=rule.created_at,
            )
        )
    return items


@router.post(
    "/admin/rules",
    response_model=RoutingRuleOut,
    dependencies=[Depends(_require_master_auth)],
)
async def create_rule(
    payload: RoutingRuleCreate, session: AsyncSession = Depends(get_session)
) -> RoutingRuleOut:
    group_name = await _ensure_rule_group_available(session, payload.group_name)
    rule = RoutingRule(
        model_pattern=payload.model_pattern,
        group_name=group_name,
        priority=payload.priority,
        is_active=payload.is_active,
        target_key_ids_json=_serialize_rule_config(
            payload.target_key_ids, payload.strategy
        ),
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    target_key_ids, strategy = _deserialize_rule_config(rule.target_key_ids_json)
    return RoutingRuleOut(
        id=rule.id,
        model_pattern=rule.model_pattern,
        group_name=rule.group_name,
        priority=rule.priority,
        strategy=strategy,
        is_active=rule.is_active,
        target_key_ids=target_key_ids,
        request_count=0,
        total_tokens=0,
        avg_ttft_ms=None,
        avg_tps=None,
        created_at=rule.created_at,
    )


@router.patch(
    "/admin/rules/{rule_id}",
    response_model=RoutingRuleOut,
    dependencies=[Depends(_require_master_auth)],
)
async def update_rule(
    rule_id: int,
    payload: RoutingRuleUpdate,
    session: AsyncSession = Depends(get_session),
) -> RoutingRuleOut:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "group_name" in data and data["group_name"] != rule.group_name:
        data["group_name"] = await _ensure_rule_group_available(
            session, data["group_name"], exclude_rule_id=rule.id
        )
    if "target_key_ids" in data or "strategy" in data:
        current_targets, current_strategy = _deserialize_rule_config(
            rule.target_key_ids_json
        )
        next_targets = data.pop("target_key_ids", current_targets)
        next_strategy = data.pop("strategy", current_strategy)
        rule.target_key_ids_json = _serialize_rule_config(next_targets, next_strategy)
    for field, value in data.items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    target_key_ids, strategy = _deserialize_rule_config(rule.target_key_ids_json)
    return RoutingRuleOut(
        id=rule.id,
        model_pattern=rule.model_pattern,
        group_name=rule.group_name,
        priority=rule.priority,
        strategy=strategy,
        is_active=rule.is_active,
        target_key_ids=target_key_ids,
        request_count=0,
        total_tokens=0,
        avg_ttft_ms=None,
        avg_tps=None,
        created_at=rule.created_at,
    )


@router.delete(
    "/admin/rules/{rule_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(_require_master_auth)],
)
async def delete_rule(
    rule_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    await session.delete(rule)
    await session.commit()
    return DeleteResponse()


@router.get(
    "/admin/rules/scan",
    response_model=list[str],
    dependencies=[Depends(_require_master_auth)],
)
async def scan_rule_models(
    pattern: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
) -> list[str]:
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        raise HTTPException(status_code=400, detail="Invalid model pattern") from exc
    result = await session.execute(
        select(ModelMap.model_alias).distinct().order_by(ModelMap.model_alias)
    )
    aliases = [row[0] for row in result.all()]
    return [alias for alias in aliases if matcher.match(alias)]


@router.get(
    "/admin/model-maps",
    response_model=list[ModelMapOut],
    dependencies=[Depends(_require_master_auth)],
)
async def list_model_maps(
    endpoint_id: int | None = Query(default=None),
    model_alias: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[ModelMapOut]:
    stmt = select(ModelMap).order_by(ModelMap.id)
    if endpoint_id is not None:
        stmt = stmt.where(ModelMap.endpoint_id == endpoint_id)
    if model_alias is not None:
        stmt = stmt.where(ModelMap.model_alias == model_alias)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post(
    "/admin/model-maps",
    response_model=ModelMapOut,
    dependencies=[Depends(_require_master_auth)],
)
async def create_model_map(
    payload: ModelMapCreate, session: AsyncSession = Depends(get_session)
) -> ModelMapOut:
    model_map = ModelMap(**payload.model_dump())
    session.add(model_map)
    await session.commit()
    await session.refresh(model_map)
    return model_map


@router.patch(
    "/admin/model-maps/{model_map_id}",
    response_model=ModelMapOut,
    dependencies=[Depends(_require_master_auth)],
)
async def update_model_map(
    model_map_id: int,
    payload: ModelMapUpdate,
    session: AsyncSession = Depends(get_session),
) -> ModelMapOut:
    model_map = await session.get(ModelMap, model_map_id)
    if not model_map:
        raise HTTPException(status_code=404, detail="Model map not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field, value in data.items():
        setattr(model_map, field, value)
    await session.commit()
    await session.refresh(model_map)
    return model_map


@router.delete(
    "/admin/model-maps/{model_map_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(_require_master_auth)],
)
async def delete_model_map(
    model_map_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    model_map = await session.get(ModelMap, model_map_id)
    if not model_map:
        raise HTTPException(status_code=404, detail="Model map not found")
    await session.delete(model_map)
    await session.commit()
    return DeleteResponse()


@router.get(
    "/admin/request-logs",
    response_model=list[RequestLogOut],
    dependencies=[Depends(_require_master_auth)],
)
async def list_request_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    model_alias: str | None = Query(default=None),
    endpoint_id: int | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    status_code: int | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[RequestLogOut]:
    stmt = select(RequestLog)
    if model_alias:
        stmt = stmt.where(RequestLog.model_alias == model_alias)
    if endpoint_id is not None:
        stmt = stmt.where(RequestLog.endpoint_id == endpoint_id)
    if api_key_id is not None:
        stmt = stmt.where(RequestLog.api_key_id == api_key_id)
    if status_code is not None:
        stmt = stmt.where(RequestLog.status_code == status_code)
    since_dt = _parse_iso_datetime(since)
    if since_dt:
        stmt = stmt.where(RequestLog.created_at >= since_dt)
    until_dt = _parse_iso_datetime(until)
    if until_dt:
        stmt = stmt.where(RequestLog.created_at <= until_dt)
    stmt = stmt.order_by(RequestLog.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def _proxy_openai_request(
    request: Request,
    session: AsyncSession,
    *,
    rewrite_model: bool = True,
    strip_rule_group_from_payload: bool = True,
) -> Response:
    _require_master_auth(request)

    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty request body")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    model_alias = payload.get("model")
    if not model_alias:
        raise HTTPException(status_code=400, detail="Missing model field")

    rule_group = payload.get("rule_group")
    if rule_group is None:
        rule_group = payload.get("rules")
    if not isinstance(rule_group, str) or not rule_group:
        rule_group = request.headers.get("X-Rule-Group", "default")
    if strip_rule_group_from_payload:
        payload.pop("rule_group", None)
        payload.pop("rules", None)
    redis = await get_redis()
    notifier = get_notifier()
    circuit_breaker = CircuitBreaker(redis, notifier=notifier)
    router_service = ModelRouter(circuit_breaker)

    candidates, effective_group = await router_service.get_candidates(
        session, model_alias, rule_group
    )

    # If no candidates, try to find endpoints with agent_node configured
    if not candidates:
        from sqlalchemy import select
        from app.db.models import Endpoint, ModelMap

        # Find endpoints with agent_node configured for this model
        agent_stmt = (
            select(Endpoint, ModelMap)
            .join(ModelMap, ModelMap.endpoint_id == Endpoint.id)
            .where(
                ModelMap.model_alias == model_alias,
                Endpoint.is_active.is_(True),
                Endpoint.agent_node.isnot(None),
                Endpoint.agent_node != "",
            )
        )
        agent_result = await session.execute(agent_stmt)
        agent_endpoints = agent_result.all()

        if agent_endpoints:
            # Create candidates from endpoints with agent_node, using real API keys
            from app.db.models import APIKey as DBAPIKey

            for endpoint, model_map in agent_endpoints:
                # Get real API keys for this endpoint
                key_stmt = select(DBAPIKey).where(
                    DBAPIKey.endpoint_id == endpoint.id,
                    DBAPIKey.is_active.is_(True),
                )
                key_result = await session.execute(key_stmt)
                api_keys = key_result.scalars().all()
                
                if not api_keys:
                    continue
                    
                for api_key in api_keys:
                    candidates.append(
                        RouteCandidate(
                            api_key=api_key,
                            endpoint=endpoint,
                            real_model=model_map.real_model,
                        )
                    )
        if not candidates:
            raise HTTPException(status_code=404, detail="No available API keys")

    request_id = uuid.uuid4().hex
    trace_id = request.headers.get("X-Trace-Id")
    if trace_id:
        trace_id = trace_id.strip()
    if not trace_id:
        trace_id = uuid.uuid4().hex
    request_start = time.perf_counter()
    client = await get_http_client()

    for candidate in candidates:
        if rewrite_model:
            upstream_payload = dict(payload)
            upstream_payload["model"] = candidate.real_model
            upstream_body = json.dumps(upstream_payload).encode("utf-8")
        else:
            upstream_payload = payload
            upstream_body = raw_body

        headers = _build_upstream_headers(request.headers, candidate.endpoint, candidate.api_key.key)
        if not any(key.lower() == "x-trace-id" for key in headers):
            headers["X-Trace-Id"] = trace_id
        url = _build_target_url(candidate.endpoint.base_url, request)
        is_stream = bool(upstream_payload.get("stream"))
        debug_headers = _build_debug_headers(request_id, trace_id, candidate, model_alias)
        agent_name = _get_agent_name(candidate.endpoint)

        if agent_name:
            agent_manager = get_agent_manager()
            try:
                agent_request = AgentRequest(
                    method=request.method,
                    url=url,
                    headers=headers,
                    body=upstream_body,
                    stream=is_stream,
                )
                agent_response = await agent_manager.send_request(agent_name, agent_request)
            except AgentUnavailableError:
                if candidate != candidates[-1]:
                    continue
                raise HTTPException(status_code=502, detail="Agent unavailable")

            status_code = agent_response.status_code or 500
            if status_code in RETRYABLE_STATUSES:
                if status_code in CIRCUIT_BREAKER_STATUSES:
                    await circuit_breaker.record_failure(candidate.api_key.id)
                if is_stream:
                    content = await agent_response.read_all()
                else:
                    content = agent_response.body
                if candidate != candidates[-1]:
                    continue
                return Response(
                    content=content,
                    status_code=status_code,
                    media_type=agent_response.headers.get("content-type"),
                    headers=_merge_headers(
                        _filter_response_headers(agent_response.headers), debug_headers
                    ),
                )

            await circuit_breaker.record_success(candidate.api_key.id)

            if is_stream:
                stream_headers = _merge_headers(
                    _filter_response_headers(agent_response.headers), debug_headers
                )

                async def agent_stream_generator() -> AsyncGenerator[bytes, None]:
                    buffer = ""
                    usage_payload = None
                    first_data_at: float | None = None
                    async for chunk in agent_response.iter_bytes():
                        if chunk:
                            buffer, usage_payload, data_seen = _inspect_stream_chunk(
                                buffer, usage_payload, chunk
                            )
                            if data_seen and first_data_at is None:
                                first_data_at = time.perf_counter()
                        yield chunk
                    stream_end = time.perf_counter()
                    ttft_ms = (
                        int((first_data_at - request_start) * 1000)
                        if first_data_at is not None
                        else None
                    )
                    prompt_tokens, completion_tokens, total_tokens = extract_usage(
                        usage_payload
                    )
                    tps = _calculate_tps(first_data_at, stream_end, completion_tokens)
                    latency_ms = (
                        ttft_ms
                        if ttft_ms is not None
                        else int((stream_end - request_start) * 1000)
                    )
                    metrics = RequestMetrics(
                        request_id=request_id,
                        trace_id=trace_id,
                        model_alias=model_alias,
                        endpoint_id=candidate.endpoint.id,
                        api_key_id=candidate.api_key.id,
                        rule_group=effective_group,
                        status_code=status_code,
                        latency_ms=latency_ms,
                        ttft_ms=ttft_ms,
                        tps=tps,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    )
                    asyncio.create_task(write_request_log(metrics))

                return StreamingResponse(
                    agent_stream_generator(),
                    status_code=status_code,
                    media_type=agent_response.headers.get("content-type"),
                    headers=stream_headers,
                )

            latency_ms = int((time.perf_counter() - request_start) * 1000)
            response_payload = None
            try:
                response_payload = json.loads(agent_response.body)
            except json.JSONDecodeError:
                response_payload = None

            prompt_tokens, completion_tokens, total_tokens = extract_usage(response_payload)
            metrics = RequestMetrics(
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                endpoint_id=candidate.endpoint.id,
                api_key_id=candidate.api_key.id,
                rule_group=effective_group,
                status_code=status_code,
                latency_ms=latency_ms,
                ttft_ms=None,
                tps=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            asyncio.create_task(write_request_log(metrics))

            if response_payload is None:
                return Response(
                    content=agent_response.body,
                    status_code=status_code,
                    media_type=agent_response.headers.get("content-type"),
                    headers=_merge_headers(
                        _filter_response_headers(agent_response.headers), debug_headers
                    ),
                )

            return JSONResponse(
                status_code=status_code,
                content=response_payload,
                headers=_merge_headers(
                    _filter_response_headers(agent_response.headers), debug_headers
                ),
            )

        try:
            request_obj = client.build_request(
                request.method,
                url,
                headers=headers,
                content=upstream_body,
            )
            response = await client.send(request_obj, stream=is_stream)
        except Exception as exc:
            await circuit_breaker.record_failure(candidate.api_key.id)
            if candidate != candidates[-1]:
                continue
            raise HTTPException(status_code=502, detail="Upstream connection error") from exc

        if response.status_code in RETRYABLE_STATUSES:
            if response.status_code in CIRCUIT_BREAKER_STATUSES:
                await circuit_breaker.record_failure(candidate.api_key.id)
            content = await response.aread()
            await response.aclose()
            if candidate != candidates[-1]:
                continue
            return Response(
                content=content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type"),
                headers=_merge_headers(
                    _filter_response_headers(response.headers), debug_headers
                ),
            )

        await circuit_breaker.record_success(candidate.api_key.id)
        latency_ms = int((time.perf_counter() - request_start) * 1000)

        if is_stream:
            stream_headers = _merge_headers(
                _filter_response_headers(response.headers), debug_headers
            )
            generator = _stream_response(
                response=response,
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                endpoint_id=candidate.endpoint.id,
                api_key_id=candidate.api_key.id,
                rule_group=effective_group,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_start=request_start,
            )
            return StreamingResponse(
                generator,
                status_code=response.status_code,
                media_type=response.headers.get("content-type"),
                headers=stream_headers,
            )

        content = await response.aread()
        response_payload = None
        try:
            response_payload = json.loads(content)
        except json.JSONDecodeError:
            response_payload = None

        prompt_tokens, completion_tokens, total_tokens = extract_usage(response_payload)
        metrics = RequestMetrics(
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            endpoint_id=candidate.endpoint.id,
            api_key_id=candidate.api_key.id,
            rule_group=effective_group,
            status_code=response.status_code,
            latency_ms=latency_ms,
            ttft_ms=None,
            tps=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        asyncio.create_task(write_request_log(metrics))

        if response_payload is None:
            return Response(
                content=content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type"),
                headers=_merge_headers(
                    _filter_response_headers(response.headers), debug_headers
                ),
            )

        return JSONResponse(
            status_code=response.status_code,
            content=response_payload,
            headers=_merge_headers(
                _filter_response_headers(response.headers), debug_headers
            ),
        )

    raise HTTPException(status_code=502, detail="All upstream requests failed")


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(request, session)


@router.post("/v1/completions")
async def completions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(request, session)


@router.post("/v1/embeddings")
async def embeddings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(request, session)


@router.post("/v1/responses")
async def responses(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=False,
        strip_rule_group_from_payload=False,
    )


def _build_upstream_headers(
    incoming_headers: dict, endpoint: object, api_key: str
) -> dict:
    headers = {}
    skip_headers = {"host", "content-length", "authorization"}
    for key, value in incoming_headers.items():
        if key.lower() in skip_headers:
            continue
        headers[key] = value

    header_name = getattr(endpoint, "auth_header_name", "Authorization") or "Authorization"
    header_prefix = getattr(endpoint, "auth_header_prefix", "Bearer")
    if header_prefix:
        headers[header_name] = f"{header_prefix} {api_key}"
    else:
        headers[header_name] = api_key
    return headers


def _get_agent_name(endpoint: object) -> str | None:
    name = getattr(endpoint, "agent_node", None)
    if not name:
        return None
    trimmed = str(name).strip()
    return trimmed or None


def _build_target_url(base_url: str, request: Request) -> str:
    base = base_url.rstrip("/")
    path = request.url.path
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    url = f"{base}{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return url


def _filter_response_headers(headers: dict) -> dict:
    excluded = {
        "content-encoding",
        "transfer-encoding",
        "connection",
        "content-type",
    }
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


def _build_debug_headers(
    request_id: str,
    trace_id: str,
    candidate: RouteCandidate,
    model_alias: str,
) -> dict:
    return {
        "x-request-id": request_id,
        "x-trace-id": trace_id,
        "x-endpoint-id": str(candidate.endpoint.id),
        "x-endpoint-name": candidate.endpoint.name,
        "x-api-key-id": str(candidate.api_key.id),
        "x-model-alias": model_alias,
        "x-real-model": candidate.real_model,
    }


def _merge_headers(base: dict, extra: dict) -> dict:
    merged = dict(base)
    merged.update(extra)
    sanitized: dict[str, str] = {}
    for key, value in merged.items():
        try:
            key_text = str(key)
            value_text = str(value)
            key_text.encode("latin-1")
            value_text.encode("latin-1")
        except UnicodeEncodeError:
            continue
        sanitized[key_text] = value_text
    return sanitized


async def _stream_response(
    response,
    request_id: str,
    trace_id: str,
    model_alias: str,
    endpoint_id: int,
    api_key_id: int,
    rule_group: str,
    status_code: int,
    latency_ms: int,
    request_start: float,
) -> AsyncGenerator[bytes, None]:
    buffer = ""
    usage_payload = None
    first_data_at: float | None = None
    try:
        async for chunk in response.aiter_bytes():
            if chunk:
                buffer, usage_payload, data_seen = _inspect_stream_chunk(
                    buffer, usage_payload, chunk
                )
                if data_seen and first_data_at is None:
                    first_data_at = time.perf_counter()
            yield chunk
    finally:
        stream_end = time.perf_counter()
        await response.aclose()
        ttft_ms = (
            int((first_data_at - request_start) * 1000)
            if first_data_at is not None
            else None
        )
        prompt_tokens, completion_tokens, total_tokens = extract_usage(usage_payload)
        tps = _calculate_tps(first_data_at, stream_end, completion_tokens)
        resolved_latency_ms = ttft_ms if ttft_ms is not None else latency_ms
        metrics = RequestMetrics(
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            endpoint_id=endpoint_id,
            api_key_id=api_key_id,
            rule_group=rule_group,
            status_code=status_code,
            latency_ms=resolved_latency_ms,
            ttft_ms=ttft_ms,
            tps=tps,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        asyncio.create_task(write_request_log(metrics))


def _calculate_tps(
    first_data_at: float | None, stream_end: float, completion_tokens: int | None
) -> float | None:
    if first_data_at is None or completion_tokens is None:
        return None
    if completion_tokens <= 0:
        return None
    duration = stream_end - first_data_at
    if duration <= 0:
        return None
    return completion_tokens / duration


def _inspect_stream_chunk(
    buffer: str, usage_payload: dict | None, chunk: bytes
) -> tuple[str, dict | None, bool]:
    try:
        text = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return buffer, usage_payload, False

    buffer += text
    lines = buffer.split("\n")
    buffer = lines.pop()
    data_seen = False
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        data_seen = True
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if "usage" in payload:
            usage_payload = payload
    return buffer, usage_payload, data_seen

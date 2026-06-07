from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import asyncio
import json
import re
import secrets
import shlex
from typing import Iterable, Mapping

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_models import (
    DashboardEndpointOut,
    EndpointDetailOut,
    EndpointOut,
    EndpointKeyOut,
    HealthProbeBucketOut,
    MetricsBucketOut,
    RoutingRuleOut,
)
from app.core.config import get_settings
from app.db.models import APIKey, Agent, Endpoint, FactoryAccessKey, RequestLog, RoutingRule
from app.services.agents import get_agent_by_name, verify_agent_token
from app.services.health_monitor import HealthProbeResult


VALID_ENDPOINT_ACCESS_MODES = {"direct", "via_agent"}


def _normalize_endpoint_access_mode(raw: object, agent_node: object = None) -> str:
    normalized = str(raw or "").strip().lower()
    if not normalized:
        normalized = "via_agent" if str(agent_node or "").strip() else "direct"
    if normalized not in VALID_ENDPOINT_ACCESS_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported endpoint access_mode. Allowed values: "
                f"{', '.join(sorted(VALID_ENDPOINT_ACCESS_MODES))}"
            ),
        )
    return normalized


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


def _get_header_case_insensitive(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value:
        return str(value)
    lowered = name.lower()
    for key, candidate in headers.items():
        if key.lower() == lowered and candidate is not None:
            return str(candidate)
    return None


def _extract_factory_api_key(headers: Mapping[str, str]) -> str | None:
    token = _parse_bearer_token(_get_header_case_insensitive(headers, "Authorization"))
    if token:
        return token

    x_api_key = _get_header_case_insensitive(headers, "x-api-key")
    if x_api_key:
        parsed = x_api_key.strip()
        return parsed or None

    x_goog_api_key = _get_header_case_insensitive(headers, "x-goog-api-key")
    if not x_goog_api_key:
        return None
    parsed = x_goog_api_key.strip()
    return parsed or None


def _require_master_auth(request: Request) -> None:
    settings = get_settings()
    if not settings.master_auth_token:
        return

    token = _extract_factory_api_key(request.headers)
    if token != settings.master_auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _issue_rule_access_key() -> str:
    return f"rk-{secrets.token_urlsafe(24)}"


async def _resolve_allowed_rule_groups_from_token(
    session: AsyncSession,
    request: Request,
) -> list[str]:
    """解析对外访问 Key 并返回可访问的规则组列表。"""
    settings = get_settings()
    token = _extract_factory_api_key(request.headers)
    if token and settings.master_auth_token and token == settings.master_auth_token:
        return ["default"]

    if not token:
        raise HTTPException(status_code=401, detail="Missing route API key")

    result = await session.execute(
        select(FactoryAccessKey).where(
            FactoryAccessKey.key == token,
            FactoryAccessKey.is_active.is_(True),
        )
    )
    factory_key = result.scalar_one_or_none()
    if not factory_key:
        raise HTTPException(status_code=401, detail="Invalid route API key")

    groups: list[str] = []
    seen: set[str] = set()
    for raw_group in factory_key.rule_groups:
        group = str(raw_group or "").strip()
        if not group:
            continue
        canonical = "default" if group.lower() == "default" else group
        tokenized = canonical.lower()
        if tokenized in seen:
            continue
        seen.add(tokenized)
        groups.append(canonical)

    return groups or ["default"]


async def _resolve_rule_group_from_token(
    session: AsyncSession,
    request: Request,
    payload_rule_group: str,
) -> str:
    """解析请求中的对外访问 Key，验证权限并返回可用的规则组。"""
    settings = get_settings()
    token = _extract_factory_api_key(request.headers)
    normalized_payload_group = (payload_rule_group or "default").strip() or "default"
    if token and settings.master_auth_token and token == settings.master_auth_token:
        return normalized_payload_group

    allowed_groups = await _resolve_allowed_rule_groups_from_token(session, request)
    allowed_group_map = {group.lower(): group for group in allowed_groups}
    requested_group = normalized_payload_group.lower()
    if requested_group in allowed_group_map:
        return allowed_group_map[requested_group]

    # 请求组无权限时，返回当前 Key 绑定的首个规则组
    return allowed_groups[0]


async def _find_dump_rule(
    session: AsyncSession, model_alias: str, rule_group: str
) -> RoutingRule | None:
    try:
        result = await session.execute(
            select(RoutingRule)
            .where(
                RoutingRule.group_name == rule_group,
                RoutingRule.is_active.is_(True),
                RoutingRule.dump_enabled.is_(True),
            )
            .order_by(RoutingRule.priority.desc(), RoutingRule.id)
        )
    except (AttributeError, AssertionError):
        return None

    rules = result.scalars().all()
    for rule in rules:
        try:
            if re.match(rule.model_pattern, model_alias):
                return rule
        except re.error:
            continue
    return None


def _sanitize_dump_filename(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")
    return normalized or "session"


async def _dump_proxy_record(
    rule: RoutingRule | None,
    request_id: str,
    trace_id: str,
    endpoint_name: str,
    model_alias: str,
    request_body: bytes,
    response_body: bytes,
    status_code: int,
    *,
    session_id: str | None = None,
    request_path: str | None = None,
) -> None:
    if rule is None or not rule.dump_enabled:
        return
    dump_dir_raw = (rule.dump_path or "").strip()
    if not dump_dir_raw:
        return

    dump_dir = Path(dump_dir_raw).expanduser()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target_file = dump_dir / f"{timestamp}-{request_id}.json"
    resolved_session_id = (session_id or trace_id).strip() if (session_id or trace_id) else trace_id
    if not resolved_session_id:
        resolved_session_id = "session"
    safe_session_id = _sanitize_dump_filename(resolved_session_id)
    session_file = dump_dir / f"session-{safe_session_id}.jsonl"
    payload = {
        "request_id": request_id,
        "trace_id": trace_id,
        "session_id": resolved_session_id,
        "rule_id": rule.id,
        "rule_group": rule.group_name,
        "endpoint_name": endpoint_name,
        "model_alias": model_alias,
        "request_path": request_path,
        "status_code": status_code,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request_body": request_body.decode("utf-8", errors="replace"),
        "response_body": response_body.decode("utf-8", errors="replace"),
    }

    def _write() -> None:
        dump_dir.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        target_file.write_text(serialized, encoding="utf-8")
        with session_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False))
            stream.write("\n")

    try:
        await asyncio.to_thread(_write)
    except Exception:
        # Dump is best-effort and must never break proxy traffic.
        return


def _build_agent_install_command(
    script_url: str,
    ws_url: str,
    heartbeat_url: str,
    name: str,
    token: str,
    region: str | None,
    endpoint_url: str | None,
    repo_url: str | None,
    repo_ref: str | None = None,
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
    if repo_ref:
        args.extend(["--repo-ref", repo_ref])
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

    if settings.agent_auth_token:
        if header_token == settings.agent_auth_token:
            return None
        if not token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    agent = await get_agent_by_name(session, name)

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
            rule_group=getattr(key, "primary_rule_group", key.rule_group),
            rule_groups=getattr(
                key,
                "rule_groups",
                APIKey.normalize_rule_groups(fallback=getattr(key, "rule_group", "default")),
            ),
            rpm_limit=key.rpm_limit,
            daily_limit=key.daily_limit,
            used_today=key.used_today,
            is_active=key.is_active,
        )
        for key in (endpoint.api_keys or [])
    ]
    model_count = len(endpoint.model_maps or [])

    # 解析 JSON 字段
    extra_headers = None
    if endpoint.extra_headers:
        try:
            import json
            extra_headers = json.loads(endpoint.extra_headers)
        except (json.JSONDecodeError, TypeError):
            pass

    extra_query_params = None
    if endpoint.extra_query_params:
        try:
            import json
            extra_query_params = json.loads(endpoint.extra_query_params)
        except (json.JSONDecodeError, TypeError):
            pass

    oauth_config = None
    if endpoint.oauth_config:
        try:
            import json
            oauth_config = json.loads(endpoint.oauth_config)
        except (json.JSONDecodeError, TypeError):
            pass

    return EndpointDetailOut(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        auth_header_name=endpoint.auth_header_name,
        auth_header_prefix=endpoint.auth_header_prefix,
        provider=endpoint.provider,
        strategy=endpoint.strategy,
        access_mode=_normalize_endpoint_access_mode(
            getattr(endpoint, "access_mode", None), getattr(endpoint, "agent_node", None)
        ),
        is_active=endpoint.is_active,
        status=status,
        latency=latency_ms,
        uptime=uptime,
        is_agent_enabled=(
            _normalize_endpoint_access_mode(
                getattr(endpoint, "access_mode", None), getattr(endpoint, "agent_node", None)
            )
            == "via_agent"
            and bool(endpoint.agent_node)
        ),
        agent_node=endpoint.agent_node,
        probe_interval_seconds=endpoint.probe_interval_seconds,
        url_path_suffix=endpoint.url_path_suffix,
        extra_headers=extra_headers,
        extra_cookies=endpoint.extra_cookies,
        extra_query_params=extra_query_params,
        oauth_config=oauth_config,
        request_body_template=endpoint.request_body_template,
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


def _build_endpoint_out(endpoint: Endpoint) -> EndpointOut:
    """构建 EndpointOut 响应，处理 JSON 字段转换"""
    import json

    extra_headers = None
    if endpoint.extra_headers:
        try:
            extra_headers = json.loads(endpoint.extra_headers)
        except (json.JSONDecodeError, TypeError):
            pass

    extra_query_params = None
    if endpoint.extra_query_params:
        try:
            extra_query_params = json.loads(endpoint.extra_query_params)
        except (json.JSONDecodeError, TypeError):
            pass

    oauth_config = None
    if endpoint.oauth_config:
        try:
            oauth_config = json.loads(endpoint.oauth_config)
        except (json.JSONDecodeError, TypeError):
            pass

    return EndpointOut(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        auth_header_name=endpoint.auth_header_name,
        auth_header_prefix=endpoint.auth_header_prefix,
        provider=endpoint.provider,
        strategy=endpoint.strategy,
        access_mode=_normalize_endpoint_access_mode(
            getattr(endpoint, "access_mode", None), getattr(endpoint, "agent_node", None)
        ),
        agent_node=endpoint.agent_node,
        probe_interval_seconds=endpoint.probe_interval_seconds,
        is_active=endpoint.is_active,
        url_path_suffix=endpoint.url_path_suffix,
        extra_headers=extra_headers,
        extra_cookies=endpoint.extra_cookies,
        extra_query_params=extra_query_params,
        oauth_config=oauth_config,
        request_body_template=endpoint.request_body_template,
        created_at=endpoint.created_at,
    )


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


DEFAULT_RULE_GROUP = "default"
DEFAULT_RULE_MODEL_PATTERN = ".*"
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


async def _ensure_default_rule_group(session: AsyncSession) -> RoutingRule:
    existing = await session.scalar(
        select(RoutingRule)
        .where(RoutingRule.group_name == DEFAULT_RULE_GROUP)
        .order_by(RoutingRule.id)
    )
    if existing is not None:
        return existing

    rule = RoutingRule(
        model_pattern=DEFAULT_RULE_MODEL_PATTERN,
        group_name=DEFAULT_RULE_GROUP,
        priority=0,
        is_active=True,
        dump_enabled=False,
        dump_path=None,
        target_key_ids_json=_serialize_rule_config([], DEFAULT_RULE_STRATEGY),
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


def _build_routing_rule_out(
    rule: RoutingRule,
    target_key_ids: list[int],
    strategy: str,
    request_count: int = 0,
    total_tokens: int = 0,
    avg_ttft_ms: int | None = None,
    avg_tps: float | None = None,
) -> RoutingRuleOut:
    return RoutingRuleOut(
        id=rule.id,
        model_pattern=rule.model_pattern,
        group_name=rule.group_name,
        priority=rule.priority,
        strategy=strategy,
        is_active=rule.is_active,
        dump_enabled=rule.dump_enabled,
        dump_path=rule.dump_path,
        target_key_ids=target_key_ids,
        request_count=request_count,
        total_tokens=total_tokens,
        avg_ttft_ms=avg_ttft_ms,
        avg_tps=avg_tps,
        created_at=rule.created_at,
    )


def _normalize_rule_group_name(value: str) -> str:
    return str(value or "").strip()


def _is_default_rule_group(value: str) -> bool:
    return _normalize_rule_group_name(value).lower() == DEFAULT_RULE_GROUP


async def _ensure_rule_group_available(
    session: AsyncSession, group_name: str, exclude_rule_id: int | None = None
) -> str:
    normalized = _normalize_rule_group_name(group_name)
    if not normalized or _is_default_rule_group(normalized):
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

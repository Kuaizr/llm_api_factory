from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import asyncio
import hmac
import json
import re
import secrets
import shlex
import socket
from typing import Iterable, Mapping

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_models import (
    APIKeyOut,
    DashboardEndpointOut,
    EndpointDetailOut,
    EndpointOut,
    EndpointKeyOut,
    HealthProbeBucketOut,
    MetricsBucketOut,
    RoutingRuleOut,
)
from app.core.config import get_settings
from app.core.route_exposure import (
    DEFAULT_EXPOSURE_FORMAT,
    EXPLICIT_EXPOSURE_FORMATS,
    exposure_format_match_priority,
    normalize_exposure_format,
    normalize_exposure_formats,
)
from app.core.timezone import app_today
from app.db.models import (
    APIKey,
    Agent,
    DumpIndex,
    Endpoint,
    FactoryAccessKey,
    RequestLog,
    RoutingRule,
)
from app.db.session import SessionLocal
from app.services.admin_auth import verify_admin_session_token
from app.services.access_keys import hash_access_key
from app.services.agents import get_agent_by_name, verify_agent_token
from app.services.health_monitor import HealthProbeResult
from app.services.model_patterns import model_pattern_matches
from app.services.secrets import (
    mask_oauth_config,
    mask_secret_value,
    merge_masked_oauth_config,
)


VALID_ENDPOINT_ACCESS_MODES = {"direct", "via_agent"}
DUMP_HOSTNAME = socket.gethostname()


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
    return app_today()


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


def _extract_route_api_key(request: Request) -> str | None:
    token = _extract_factory_api_key(request.headers)
    if token:
        return token

    # Gemini clients commonly use the official `?key=...` auth style.
    # Treat it as the downstream factory key, then strip it before upstream proxying.
    query_key = request.query_params.get("key")
    if query_key:
        parsed = query_key.strip()
        return parsed or None
    return None


def _require_master_auth(request: Request) -> None:
    settings = get_settings()
    if not settings.master_auth_token:
        return

    token = _extract_factory_api_key(request.headers)
    if not verify_admin_session_token(token, settings):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _issue_rule_access_key() -> str:
    return f"rk-{secrets.token_urlsafe(24)}"


async def _resolve_allowed_rule_groups_from_token(
    session: AsyncSession,
    request: Request,
) -> list[str]:
    """解析对外访问 Key 并返回可访问的规则组列表。"""
    settings = get_settings()
    token = _extract_route_api_key(request)
    if token and verify_admin_session_token(token, settings):
        request.state.route_allowed_rule_groups = ["default"]
        return ["default"]

    if not token:
        raise HTTPException(status_code=401, detail="Missing route API key")

    token_hash = hash_access_key(token)
    result = await session.execute(
        select(FactoryAccessKey).where(
            FactoryAccessKey.key.in_([token_hash, token]),
            FactoryAccessKey.is_active.is_(True),
        )
    )
    factory_key = result.scalars().first()
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

    resolved_groups = groups or ["default"]
    request.state.route_allowed_rule_groups = resolved_groups
    return resolved_groups


async def _resolve_rule_group_from_token(
    session: AsyncSession,
    request: Request,
    payload_rule_group: str,
) -> str:
    """解析请求中的对外访问 Key，验证权限并返回可用的规则组。"""
    settings = get_settings()
    token = _extract_route_api_key(request)
    normalized_payload_group = (payload_rule_group or "default").strip() or "default"
    if token and verify_admin_session_token(token, settings):
        return normalized_payload_group

    allowed_groups = await _resolve_allowed_rule_groups_from_token(session, request)
    allowed_group_map = {group.lower(): group for group in allowed_groups}
    requested_group = normalized_payload_group.lower()
    if requested_group in allowed_group_map:
        return allowed_group_map[requested_group]

    # 请求组无权限时，返回当前 Key 绑定的首个规则组
    return allowed_groups[0]


async def _find_dump_rule(
    session: AsyncSession,
    model_alias: str,
    rule_group: str,
    exposure_format: str = DEFAULT_EXPOSURE_FORMAT,
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
    fallback: RoutingRule | None = None
    for rule in rules:
        if not model_pattern_matches(rule.model_pattern, model_alias):
            continue
        _, _, rule_exposure_format = _deserialize_rule_config_detail(
            rule.target_key_ids_json
        )
        match_priority = exposure_format_match_priority(
            rule_exposure_format, exposure_format
        )
        if match_priority == 2:
            return rule
        if match_priority == 1 and fallback is None:
            fallback = rule
    return fallback


def _sanitize_dump_filename(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")
    return normalized or "session"


def _resolve_dump_directory(dump_path: str) -> Path:
    settings = get_settings()
    root = Path(settings.proxy_dump_root).expanduser().resolve()
    candidate = Path(dump_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"dump_path must stay under {root.as_posix()}",
        ) from exc
    return resolved


def _normalize_dump_path(dump_path: str | None) -> str | None:
    if dump_path is None:
        return None
    trimmed = dump_path.strip()
    if not trimmed:
        return None
    return _resolve_dump_directory(trimmed).as_posix()


def _extract_previous_interaction_id(request_body: bytes) -> str | None:
    if not request_body:
        return None
    try:
        payload = json.loads(request_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("previous_interaction_id")
    if value is None:
        value = payload.get("previousInteractionId")
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed[:128] or None


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
    endpoint_id: int | None = None,
    real_model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cached_tokens: int | None = None,
    latency_ms: int | None = None,
    is_stream: bool = False,
    stream_complete: bool | None = None,
    is_cache_hit: bool = False,
    session_id: str | None = None,
    request_path: str | None = None,
) -> None:
    if rule is None or not rule.dump_enabled:
        return
    dump_dir_raw = (rule.dump_path or "").strip()
    if not dump_dir_raw:
        return

    try:
        dump_dir = _resolve_dump_directory(dump_dir_raw)
    except HTTPException:
        return
    now = datetime.now(timezone.utc)
    hostname = _sanitize_dump_filename(DUMP_HOSTNAME)
    resolved_real_model = real_model or model_alias
    safe_real_model = _sanitize_dump_filename(resolved_real_model)
    safe_request_id = _sanitize_dump_filename(request_id)
    relative_file = (
        Path(hostname)
        / now.strftime("%Y-%m-%d")
        / safe_real_model
        / f"{safe_request_id}.json"
    )
    target_file = dump_dir / relative_file
    resolved_session_id = (session_id or trace_id).strip() if (session_id or trace_id) else trace_id
    if not resolved_session_id:
        resolved_session_id = "session"
    safe_session_id = _sanitize_dump_filename(resolved_session_id)
    session_file = dump_dir / hostname / "sessions" / f"{safe_session_id}.jsonl"
    previous_interaction_id = _extract_previous_interaction_id(request_body)
    resolved_is_cache_hit = is_cache_hit or bool((cached_tokens or 0) > 0)
    payload = {
        "request_id": request_id,
        "trace_id": trace_id,
        "session_id": resolved_session_id,
        "rule_id": rule.id,
        "rule_group": rule.group_name,
        "endpoint_id": endpoint_id,
        "endpoint_name": endpoint_name,
        "model_alias": model_alias,
        "real_model": resolved_real_model,
        "request_path": request_path,
        "status_code": status_code,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "latency_ms": latency_ms,
        "is_stream": is_stream,
        "is_cache_hit": resolved_is_cache_hit,
        "stream_complete": stream_complete,
        "previous_interaction_id": previous_interaction_id,
        "file_path": relative_file.as_posix(),
        "hostname": hostname,
        "created_at": now.isoformat(),
        "request_body": request_body.decode("utf-8", errors="replace"),
        "response_body": response_body.decode("utf-8", errors="replace"),
    }

    def _write() -> None:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.parent.mkdir(parents=True, exist_ok=True)
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
    await _write_dump_index(
        request_id=request_id,
        trace_id=trace_id,
        model_alias=model_alias,
        real_model=resolved_real_model,
        endpoint_id=endpoint_id,
        rule_group=rule.group_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        latency_ms=latency_ms,
        is_stream=is_stream,
        is_cache_hit=resolved_is_cache_hit,
        stream_complete=stream_complete,
        previous_interaction_id=previous_interaction_id,
        file_path=relative_file.as_posix(),
        hostname=hostname,
    )


async def _write_dump_index(
    *,
    request_id: str,
    trace_id: str,
    model_alias: str,
    real_model: str,
    endpoint_id: int | None,
    rule_group: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    cached_tokens: int | None,
    latency_ms: int | None,
    is_stream: bool,
    is_cache_hit: bool,
    stream_complete: bool | None,
    previous_interaction_id: str | None,
    file_path: str,
    hostname: str,
) -> None:
    if endpoint_id is None:
        return
    async with SessionLocal() as session:
        try:
            session.add(
                DumpIndex(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    real_model=real_model,
                    endpoint_id=endpoint_id,
                    rule_group=rule_group,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cached_tokens=cached_tokens,
                    latency_ms=latency_ms,
                    is_stream=is_stream,
                    is_cache_hit=is_cache_hit,
                    stream_complete=stream_complete,
                    previous_interaction_id=previous_interaction_id,
                    file_path=file_path,
                    hostname=hostname,
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
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
    allowed_targets: str | None = None,
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
    if allowed_targets:
        args.extend(["--allowed-targets", allowed_targets])
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
        if header_token and hmac.compare_digest(header_token, settings.agent_auth_token):
            return None
        if not token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    agent = await get_agent_by_name(session, name)
    if agent is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if agent and agent.auth_token_hash and token:
        if verify_agent_token(token, agent.auth_token_hash):
            return agent

    if not settings.agent_auth_token and (agent is None or agent.auth_token_hash is None):
        return agent

    raise HTTPException(status_code=401, detail="Unauthorized")


def _mask_key(value: str) -> str:
    return mask_secret_value(value, settings=get_settings())


def _merge_masked_oauth_config(existing_raw: str | None, incoming: dict[str, str]) -> dict[str, str]:
    return merge_masked_oauth_config(existing_raw, incoming)


def _build_api_key_out(api_key: APIKey) -> APIKeyOut:
    return APIKeyOut(
        id=api_key.id,
        endpoint_id=api_key.endpoint_id,
        key=_mask_key(api_key.key),
        name=api_key.name,
        rule_group=api_key.primary_rule_group,
        rule_groups=api_key.rule_groups,
        weight=api_key.weight,
        rpm_limit=api_key.rpm_limit,
        daily_limit=api_key.daily_limit,
        used_today=api_key.used_today,
        total_usage=api_key.total_usage,
        is_active=api_key.is_active,
        created_at=api_key.created_at,
    )


def _build_endpoint_detail(
    endpoint: Endpoint,
    status: str,
    latency_ms: int,
    uptime: float,
    codex_usage_by_key: dict[int, dict[str, object]] | None = None,
) -> EndpointDetailOut:
    codex_usage_by_key = codex_usage_by_key or {}
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
            codex_usage=codex_usage_by_key.get(key.id),
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
            oauth_config = mask_oauth_config(json.loads(endpoint.oauth_config))
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
            oauth_config = mask_oauth_config(json.loads(endpoint.oauth_config))
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
        # Public dashboard data must never expose the upstream address. The
        # authenticated admin endpoint returns the real value when needed.
        base_url="hidden",
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


def _serialize_rule_config(
    target_key_ids: list[int],
    strategy: str,
    exposure_formats: list[str],
) -> str:
    normalized_formats = normalize_exposure_formats(exposure_formats)
    return json.dumps({
        "target_key_ids": target_key_ids,
        "strategy": strategy,
        "exposure_formats": normalized_formats,
    })


def _deserialize_rule_config_detail(raw: str) -> tuple[list[int], str, list[str]]:
    if not raw:
        return [], DEFAULT_RULE_STRATEGY, []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], DEFAULT_RULE_STRATEGY, []
    if isinstance(data, dict):
        target_key_ids = _parse_target_key_ids(data.get("target_key_ids", []))
        strategy = data.get("strategy") or DEFAULT_RULE_STRATEGY
        if not isinstance(strategy, str):
            strategy = str(strategy)
        return (
            target_key_ids,
            strategy,
            normalize_exposure_formats(data.get("exposure_formats", [])),
        )
    return [], DEFAULT_RULE_STRATEGY, []


def _deserialize_rule_config(raw: str) -> tuple[list[int], str]:
    target_key_ids, strategy, _ = _deserialize_rule_config_detail(raw)
    return target_key_ids, strategy


async def _ensure_default_rule_group(
    session: AsyncSession, *, commit: bool = True
) -> RoutingRule:
    existing = await session.scalar(
        select(RoutingRule)
        .where(RoutingRule.group_name == DEFAULT_RULE_GROUP)
        .order_by(RoutingRule.id)
    )
    if existing is not None:
        target_key_ids, strategy, exposure_formats = _deserialize_rule_config_detail(
            existing.target_key_ids_json
        )
        changed = False
        if target_key_ids or exposure_formats != list(EXPLICIT_EXPOSURE_FORMATS):
            existing.target_key_ids_json = _serialize_rule_config(
                [], strategy, list(EXPLICIT_EXPOSURE_FORMATS)
            )
            changed = True
        if existing.model_pattern != DEFAULT_RULE_MODEL_PATTERN:
            existing.model_pattern = DEFAULT_RULE_MODEL_PATTERN
            changed = True
        if not existing.is_active:
            existing.is_active = True
            changed = True
        if changed:
            if commit:
                await session.commit()
                await session.refresh(existing)
            else:
                await session.flush()
        return existing

    rule = RoutingRule(
        model_pattern=DEFAULT_RULE_MODEL_PATTERN,
        group_name=DEFAULT_RULE_GROUP,
        priority=0,
        is_active=True,
        dump_enabled=False,
        dump_path=None,
        target_key_ids_json=_serialize_rule_config(
            [], DEFAULT_RULE_STRATEGY, list(EXPLICIT_EXPOSURE_FORMATS)
        ),
    )
    session.add(rule)
    if commit:
        await session.commit()
        await session.refresh(rule)
    else:
        await session.flush()
    return rule


def _build_routing_rule_out(
    rule: RoutingRule,
    target_key_ids: list[int],
    strategy: str,
    exposure_formats: list[str],
    request_count: int = 0,
    total_tokens: int = 0,
    avg_ttft_ms: int | None = None,
    avg_tps: float | None = None,
) -> RoutingRuleOut:
    normalized_formats = normalize_exposure_formats(exposure_formats)
    return RoutingRuleOut(
        id=rule.id,
        model_pattern=rule.model_pattern,
        group_name=rule.group_name,
        exposure_formats=normalized_formats,
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
    session: AsyncSession,
    group_name: str,
    exposure_formats: list[str],
    exclude_rule_id: int | None = None,
) -> str:
    normalized = _normalize_rule_group_name(group_name)
    if not normalized or _is_default_rule_group(normalized):
        raise HTTPException(status_code=400, detail="Invalid rule group name")
    normalized_exposures = set(normalize_exposure_formats(exposure_formats))
    result = await session.execute(select(RoutingRule))
    canonical_group_name = normalized
    for rule in result.scalars().all():
        if exclude_rule_id is not None and rule.id == exclude_rule_id:
            continue
        if _normalize_rule_group_name(rule.group_name).lower() != normalized.lower():
            continue
        canonical_group_name = rule.group_name
        _, _, existing_exposures = _deserialize_rule_config_detail(
            rule.target_key_ids_json
        )
        if normalized_exposures.intersection(existing_exposures):
            raise HTTPException(
                status_code=400,
                detail="Rule group and exposure formats overlap an existing rule",
            )
    return canonical_group_name


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

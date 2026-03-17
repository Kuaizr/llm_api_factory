from datetime import datetime, timezone
import re
import time

from fastapi import Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.route_helpers import (
    _build_endpoint_detail,
    _build_endpoint_out,
    _build_routing_rule_out,
    _build_rule_access_key_preview,
    _deserialize_rule_config,
    _ensure_default_rule_group,
    _ensure_rule_group_available,
    _is_default_rule_group,
    _issue_rule_access_key,
    _mask_key,
    _normalize_api_key_usage,
    _parse_iso_datetime,
    _resolve_endpoint_status,
    _serialize_rule_config,
    _today_utc_date,
)
from app.api.v1.route_models import (
    APIKeyCreate,
    APIKeyOut,
    APIKeyUpdate,
    DeleteResponse,
    EndpointCreate,
    EndpointDetailOut,
    EndpointKeyCreate,
    EndpointOut,
    EndpointProbeOut,
    EndpointUpdate,
    ModelMapCreate,
    ModelMapOut,
    ModelMapUpdate,
    RequestLogOut,
    RoutingRuleCreate,
    RoutingRuleOut,
    RoutingRuleUpdate,
    RuleAccessKeyCreate,
    RuleAccessKeyIssueOut,
    RuleAccessKeyOut,
    RuleAccessKeyPreviewOut,
    RuleAccessKeyUpdate,
)
from app.core.config import get_settings
from app.core.http_client import get_http_client
from app.core.redis import get_redis
from app.db.models import APIKey, Endpoint, ModelMap, RequestLog, RoutingRule, RuleAccessKey
from app.db.session import get_session
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import HealthProbeResult, HealthProbeStore

SUPPORTED_ENDPOINT_PROVIDERS = {"openai", "anthropic", "custom"}
ANTHROPIC_PROBE_FALLBACK_MODEL = "claude-3-5-haiku-latest"


def _normalize_endpoint_provider(raw: object) -> str:
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return "openai"
    if normalized not in SUPPORTED_ENDPOINT_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported provider. Allowed values: "
                f"{', '.join(sorted(SUPPORTED_ENDPOINT_PROVIDERS))}"
            ),
        )
    return normalized


def _normalize_url_path_suffix(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    return trimmed if trimmed.startswith("/") else f"/{trimmed}"


def _build_provider_probe_url(
    endpoint: Endpoint,
    *,
    default_suffix: str,
) -> str:
    custom_suffix = _normalize_url_path_suffix(endpoint.url_path_suffix)
    if custom_suffix:
        return f"{endpoint.base_url.rstrip('/')}{custom_suffix}"

    cleaned = endpoint.base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    return f"{cleaned}{default_suffix}"


def _pick_probe_model(existing_models: list[ModelMap]) -> str:
    for model in existing_models:
        if model.real_model:
            return model.real_model
        if model.model_alias:
            return model.model_alias
    return ANTHROPIC_PROBE_FALLBACK_MODEL


def _resolve_probe_message(
    *,
    status_code: int | None,
    discovered_models: list[str],
) -> str | None:
    if status_code in {401, 403}:
        return "API Key 权限问题，请检查上游权限配置。"
    if discovered_models:
        return None
    return "上游不支持 /v1/models 接口，请在右侧手动新增模型映射。"


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


async def create_endpoint(
    payload: EndpointCreate, session: AsyncSession = Depends(get_session)
) -> EndpointOut:
    import json
    data = payload.model_dump()
    if data.get("agent_node") == "":
        data["agent_node"] = None
    data["provider"] = _normalize_endpoint_provider(data.get("provider"))
    # 将 dict 字段转为 JSON 字符串存储
    if data.get("extra_headers") is not None:
        data["extra_headers"] = json.dumps(data["extra_headers"])
    if data.get("extra_query_params") is not None:
        data["extra_query_params"] = json.dumps(data["extra_query_params"])
    if data.get("oauth_config") is not None:
        data["oauth_config"] = json.dumps(data["oauth_config"])
    # request_body_template 直接存储字符串，无需序列化
    endpoint = Endpoint(**data)
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    return _build_endpoint_out(endpoint)


async def update_endpoint(
    endpoint_id: int,
    payload: EndpointUpdate,
    session: AsyncSession = Depends(get_session),
) -> EndpointOut:
    import json
    endpoint = await session.get(Endpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if data.get("agent_node") == "":
        data["agent_node"] = None
    if "provider" in data:
        data["provider"] = _normalize_endpoint_provider(data.get("provider"))
    # 将 dict 字段转为 JSON 字符串存储
    if "extra_headers" in data and data["extra_headers"] is not None:
        data["extra_headers"] = json.dumps(data["extra_headers"])
    if "extra_query_params" in data and data["extra_query_params"] is not None:
        data["extra_query_params"] = json.dumps(data["extra_query_params"])
    if "oauth_config" in data and data["oauth_config"] is not None:
        data["oauth_config"] = json.dumps(data["oauth_config"])
    # request_body_template 直接存储字符串，无需序列化
    for field, value in data.items():
        setattr(endpoint, field, value)
    await session.commit()
    await session.refresh(endpoint)
    return _build_endpoint_out(endpoint)


async def probe_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_session),
) -> EndpointProbeOut:
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

    existing_stmt = select(ModelMap).where(ModelMap.endpoint_id == endpoint_id).order_by(ModelMap.id)
    existing_result = await session.execute(existing_stmt)
    existing_models = existing_result.scalars().all()
    provider = _normalize_endpoint_provider(endpoint.provider)

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
    default_suffix = "/v1/messages" if provider == "anthropic" else "/v1/models"
    url = _build_provider_probe_url(endpoint, default_suffix=default_suffix)
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
    discovered_models: list[str] = []
    response = None
    started_at = time.perf_counter()
    probe_model = _pick_probe_model(existing_models)
    anthropic_probe_payload = {
        "model": probe_model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }

    try:
        if provider == "anthropic":
            response = await client.post(
                url,
                headers=headers,
                json=anthropic_probe_payload,
                timeout=settings.health_probe_timeout_seconds,
            )
        else:
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
            status = "success"
            if provider != "anthropic":
                payload = response.json()
                payload_models = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(payload_models, list):
                    discovered_models = [
                        str(item.get("id"))
                        for item in payload_models
                        if isinstance(item, dict) and item.get("id")
                    ]
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
            checked_at=datetime.now(timezone.utc),
        )
    )

    probe_message = _resolve_probe_message(
        status_code=status_code,
        discovered_models=discovered_models,
    )
    return EndpointProbeOut(
        provider=provider,
        probe_status=status,
        probe_status_code=status_code,
        probe_message=probe_message,
        discovered_models=discovered_models,
        manual_models=[ModelMapOut.model_validate(model) for model in existing_models],
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


async def create_api_key(
    payload: APIKeyCreate, session: AsyncSession = Depends(get_session)
) -> APIKeyOut:
    api_key = APIKey(**payload.model_dump())
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return api_key


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


async def delete_api_key(
    api_key_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    api_key = await session.get(APIKey, api_key_id)
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    await session.delete(api_key)
    await session.commit()
    return DeleteResponse()


async def list_rules(session: AsyncSession = Depends(get_session)) -> list[RoutingRuleOut]:
    await _ensure_default_rule_group(session)
    result = await session.execute(
        select(RoutingRule).order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    rules = result.scalars().all()
    access_result = await session.execute(
        select(RuleAccessKey).order_by(RuleAccessKey.rule_id, RuleAccessKey.id)
    )
    access_keys_by_rule: dict[int, list[RuleAccessKeyPreviewOut]] = {}
    for item in access_result.scalars().all():
        access_keys_by_rule.setdefault(item.rule_id, []).append(
            _build_rule_access_key_preview(item)
        )

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
            _build_routing_rule_out(
                rule,
                target_key_ids=target_key_ids,
                strategy=strategy,
                access_keys=access_keys_by_rule.get(rule.id, []),
                request_count=request_count,
                total_tokens=total_tokens,
                avg_ttft_ms=avg_ttft_ms,
                avg_tps=avg_tps,
            )
        )
    return items


async def create_rule(
    payload: RoutingRuleCreate, session: AsyncSession = Depends(get_session)
) -> RoutingRuleOut:
    group_name = await _ensure_rule_group_available(session, payload.group_name)
    rule = RoutingRule(
        model_pattern=payload.model_pattern,
        group_name=group_name,
        priority=payload.priority,
        is_active=payload.is_active,
        dump_enabled=payload.dump_enabled,
        dump_path=payload.dump_path,
        target_key_ids_json=_serialize_rule_config(
            payload.target_key_ids, payload.strategy
        ),
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    target_key_ids, strategy = _deserialize_rule_config(rule.target_key_ids_json)
    return _build_routing_rule_out(
        rule,
        target_key_ids=target_key_ids,
        strategy=strategy,
        access_keys=[],
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

    if _is_default_rule_group(rule.group_name):
        if data.get("group_name") is not None and not _is_default_rule_group(
            data["group_name"]
        ):
            raise HTTPException(
                status_code=400,
                detail="Default rule group cannot be renamed",
            )
        if data.get("is_active") is False:
            raise HTTPException(
                status_code=400,
                detail="Default rule group cannot be disabled",
            )

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
    access_result = await session.execute(
        select(RuleAccessKey)
        .where(RuleAccessKey.rule_id == rule.id)
        .order_by(RuleAccessKey.id)
    )
    access_keys = [
        _build_rule_access_key_preview(item) for item in access_result.scalars().all()
    ]
    return _build_routing_rule_out(
        rule,
        target_key_ids=target_key_ids,
        strategy=strategy,
        access_keys=access_keys,
    )


async def delete_rule(
    rule_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    if _is_default_rule_group(rule.group_name):
        raise HTTPException(
            status_code=400,
            detail="Default rule group cannot be deleted",
        )
    await session.delete(rule)
    await session.commit()
    return DeleteResponse()


async def list_rule_access_keys(
    rule_id: int, session: AsyncSession = Depends(get_session)
) -> list[RuleAccessKeyOut]:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")
    result = await session.execute(
        select(RuleAccessKey)
        .where(RuleAccessKey.rule_id == rule_id)
        .order_by(RuleAccessKey.id)
    )
    return [
        RuleAccessKeyOut(
            id=item.id,
            rule_id=item.rule_id,
            name=item.name,
            key_preview=_mask_key(item.key),
            key=item.key,
            is_active=item.is_active,
            created_at=item.created_at,
        )
        for item in result.scalars().all()
    ]


async def create_rule_access_key(
    rule_id: int,
    payload: RuleAccessKeyCreate,
    session: AsyncSession = Depends(get_session),
) -> RuleAccessKeyIssueOut:
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found")

    raw_key = _issue_rule_access_key()
    item = RuleAccessKey(
        rule_id=rule_id,
        name=payload.name,
        key=raw_key,
        is_active=True,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return RuleAccessKeyIssueOut(
        id=item.id,
        rule_id=item.rule_id,
        name=item.name,
        key=raw_key,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def update_rule_access_key(
    access_key_id: int,
    payload: RuleAccessKeyUpdate,
    session: AsyncSession = Depends(get_session),
) -> RuleAccessKeyOut:
    item = await session.get(RuleAccessKey, access_key_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route access key not found")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    for field, value in data.items():
        setattr(item, field, value)
    await session.commit()
    await session.refresh(item)
    return RuleAccessKeyOut(
        id=item.id,
        rule_id=item.rule_id,
        name=item.name,
        key_preview=_mask_key(item.key),
        key=item.key,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def rotate_rule_access_key(
    access_key_id: int, session: AsyncSession = Depends(get_session)
) -> RuleAccessKeyIssueOut:
    item = await session.get(RuleAccessKey, access_key_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route access key not found")
    raw_key = _issue_rule_access_key()
    item.key = raw_key
    await session.commit()
    await session.refresh(item)
    return RuleAccessKeyIssueOut(
        id=item.id,
        rule_id=item.rule_id,
        name=item.name,
        key=raw_key,
        is_active=item.is_active,
        created_at=item.created_at,
    )


async def delete_rule_access_key(
    access_key_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    item = await session.get(RuleAccessKey, access_key_id)
    if not item:
        raise HTTPException(status_code=404, detail="Route access key not found")
    await session.delete(item)
    await session.commit()
    return DeleteResponse()


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


async def create_model_map(
    payload: ModelMapCreate, session: AsyncSession = Depends(get_session)
) -> ModelMapOut:
    model_map = ModelMap(**payload.model_dump())
    session.add(model_map)
    await session.commit()
    await session.refresh(model_map)
    return model_map


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


async def delete_model_map(
    model_map_id: int, session: AsyncSession = Depends(get_session)
) -> DeleteResponse:
    model_map = await session.get(ModelMap, model_map_id)
    if not model_map:
        raise HTTPException(status_code=404, detail="Model map not found")
    await session.delete(model_map)
    await session.commit()
    return DeleteResponse()


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

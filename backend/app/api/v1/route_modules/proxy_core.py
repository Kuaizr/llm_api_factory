from typing import Callable
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import (
    _find_dump_rule,
    _resolve_rule_group_from_token,
)
from app.api.v1.route_modules.proxy_agent_handler import (
    handle_agent_candidate,
)
from app.api.v1.route_modules.proxy_context import prepare_candidate_request_context
from app.api.v1.route_modules.proxy_direct_handler import (
    handle_direct_candidate,
)
from app.api.v1.route_modules.proxy_payloads import (
    extract_requested_rule_group,
    parse_request_payload,
    resolve_model_alias,
)
from app.api.v1.route_modules.proxy_trace import (
    include_debug_headers,
    resolve_session_id,
    resolve_trace_id,
)
from app.api.v1.route_proxy_helpers import _looks_like_codex_request
from app.core.http_client import get_http_client
from app.core.redis import get_redis
from app.core.route_exposure import (
    DEFAULT_EXPOSURE_FORMAT,
    EXPOSURE_FORMAT_CODEX,
    normalize_exposure_format,
)
from app.services.circuit_breaker import CircuitBreaker
from app.services.notifications import get_notifier
from app.services.router import ModelRouter, RouteCandidate


async def _proxy_openai_request(
    request: Request,
    session: AsyncSession,
    *,
    rewrite_model: bool = True,
    strip_rule_group_from_payload: bool = False,
    path_prefix: str | None = None,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
    exposure_format: str = DEFAULT_EXPOSURE_FORMAT,
    detect_codex_exposure: bool = False,
    allow_missing_model: bool = False,
    model_alias_override: str | None = None,
    model_payload_keys: tuple[str, ...] = ("model",),
    target_path_rewriter: Callable[[str, RouteCandidate], str] | None = None,
) -> Response:
    raw_body = await request.body()
    payload = parse_request_payload(raw_body)
    model_alias = resolve_model_alias(
        request,
        payload,
        rewrite_model=rewrite_model,
        allow_missing_model=allow_missing_model,
        model_alias_override=model_alias_override,
        model_payload_keys=model_payload_keys,
    )
    requested_rule_group = extract_requested_rule_group(request, payload)
    rule_group = await _resolve_rule_group_from_token(
        session, request, requested_rule_group
    )
    allowed_rule_groups = [
        str(group).strip().lower()
        for group in getattr(request.state, "route_allowed_rule_groups", [])
        if str(group).strip()
    ]
    allow_default_rule_fallback = "default" in allowed_rule_groups
    if strip_rule_group_from_payload:
        payload.pop("rule_group", None)
        payload.pop("rules", None)
    requested_exposure_format = normalize_exposure_format(exposure_format)
    if detect_codex_exposure and _looks_like_codex_request(request.headers, payload):
        requested_exposure_format = EXPOSURE_FORMAT_CODEX

    redis = await get_redis()
    notifier = get_notifier()
    circuit_breaker = CircuitBreaker(redis, notifier=notifier)
    router_service = ModelRouter(circuit_breaker)

    candidates, effective_group = await router_service.get_candidates(
        session,
        model_alias,
        rule_group,
        provider_filters=provider_filter,
        provider_filter_fallback_to_any=provider_filter_fallback_to_any,
        allow_unmapped_fallback=True,
        allow_default_rule_fallback=allow_default_rule_fallback,
        exposure_format=requested_exposure_format,
    )

    if not candidates:
        raise HTTPException(status_code=404, detail="No available API keys")

    dump_rule = await _find_dump_rule(
        session,
        model_alias,
        effective_group,
        exposure_format=requested_exposure_format,
    )

    request_id = uuid.uuid4().hex
    session_id = resolve_session_id(request, payload)
    trace_id = resolve_trace_id(request, payload, session_id)
    request_start = time.perf_counter()
    include_internal_debug = include_debug_headers(request)
    client = await get_http_client()
    attempt_order = 0

    for candidate in candidates:
        try:
            candidate_context = await prepare_candidate_request_context(
                request,
                session,
                payload,
                raw_body,
                candidate,
                rewrite_model=rewrite_model,
                trace_id=trace_id,
                request_id=request_id,
                model_alias=model_alias,
                include_internal_debug=include_internal_debug,
                path_prefix=path_prefix,
                target_path_rewriter=target_path_rewriter,
                model_payload_keys=model_payload_keys,
                redis=redis,
                client=client,
            )
        except Exception as exc:
            await circuit_breaker.record_failure(candidate.api_key.id)
            if candidate != candidates[-1]:
                continue
            raise HTTPException(status_code=502, detail="OAuth token refresh failed") from exc

        if candidate_context.agent_name:
            result = await handle_agent_candidate(
                request=request,
                candidate=candidate,
                last_candidate=candidates[-1],
                candidate_context=candidate_context,
                router_service=router_service,
                circuit_breaker=circuit_breaker,
                redis=redis,
                client=client,
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                requested_rule_group=requested_rule_group,
                effective_group=effective_group,
                dump_rule=dump_rule,
                session_id=session_id,
                request_start=request_start,
                attempt_order=attempt_order,
            )
        else:
            result = await handle_direct_candidate(
                request=request,
                candidate=candidate,
                last_candidate=candidates[-1],
                candidate_context=candidate_context,
                router_service=router_service,
                circuit_breaker=circuit_breaker,
                client=client,
                redis=redis,
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                requested_rule_group=requested_rule_group,
                effective_group=effective_group,
                dump_rule=dump_rule,
                session_id=session_id,
                request_start=request_start,
                attempt_order=attempt_order,
            )

        attempt_order = result.attempt_order
        if result.response is not None:
            return result.response

    raise HTTPException(status_code=502, detail="All upstream requests failed")

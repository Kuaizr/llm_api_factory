from typing import AsyncGenerator, Callable
import json
import re
import time
import uuid
from urllib.parse import quote, unquote

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import (
    _dump_proxy_record,
    _extract_factory_api_key,
    _find_dump_rule,
    _resolve_allowed_rule_groups_from_token,
    _resolve_rule_group_from_token,
)
from app.api.v1.route_proxy_helpers import (
    _apply_oauth_access_token,
    _apply_request_body_template,
    _build_debug_headers,
    _build_target_url,
    _build_upstream_headers,
    _calculate_tps,
    _filter_response_headers,
    _get_agent_name,
    _inspect_stream_chunk,
    _merge_headers,
    _stream_response,
)
from app.core.config import get_settings
from app.core.http_client import get_http_client
from app.core.providers import normalize_provider_filters, normalize_provider_name
from app.core.redis import get_redis
from app.db.models import Endpoint, ModelMap, RoutingRule
from app.db.session import get_session
from app.services.admin_auth import verify_admin_session_token
from app.services.agent_transport import AgentRequest, AgentUnavailableError, get_agent_manager
from app.services.background_tasks import safe_create_task
from app.services.billing import (
    RequestAttemptMetrics,
    RequestMetrics,
    extract_usage,
    write_request_attempt_log,
    write_request_log,
)
from app.services.circuit_breaker import CircuitBreaker
from app.services.model_patterns import model_pattern_matches
from app.services.notifications import get_notifier
from app.services.router import ModelRouter, RouteCandidate

CANDIDATE_FALLBACK_STATUSES = {400, 401, 402, 403, 404, 429, 500, 502, 503, 504}
CIRCUIT_BREAKER_STATUSES = {401, 402, 403, 429, 500, 502, 503, 504}
LOCAL_RETRY_STATUSES = {429, 500, 502, 503, 504}
UPSTREAM_CANDIDATE_MAX_ATTEMPTS = 3
SEMANTIC_FAILURE_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}
SEMANTIC_FAILURE_MARKERS = (
    "error",
    "failed",
    "failure",
    "invalid",
    "unauthorized",
    "forbidden",
    "permission",
    "insufficient",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "unavailable",
    "overloaded",
    "busy",
    "timeout",
    "not supported",
    "unsupported",
    "model_not_supported",
    "service unavailable",
    "余额",
    "不足",
    "不可用",
    "限流",
    "风控",
    "失败",
    "错误",
    "封禁",
    "欠费",
)
SEMANTIC_SUCCESS_SIGNAL_KEYS = {
    "choices",
    "output",
    "output_text",
    "content",
    "candidates",
    "data",
    "embedding",
    "embeddings",
    "id",
}
SESSION_HINT_KEYS = (
    "session_id",
    "conversation_id",
    "thread_id",
    "chat_id",
    "dialog_id",
    "previous_response_id",
)
TRACE_HINT_KEYS = ("trace_id", "request_id")
GEMINI_MODEL_PATH_PATTERN = re.compile(
    r"(?P<prefix>/(?:v1|v1beta|v1alpha)/(?P<collection>models|tunedModels)/)"
    r"(?P<model>[^:?#]+)"
    r"(?P<suffix>:[^/?#]+)?"
)


def _include_debug_headers(request: Request) -> bool:
    debug_value = str(request.headers.get("X-Debug") or "").strip().lower()
    if debug_value not in {"1", "true", "yes"}:
        return False
    token = _extract_factory_api_key(request.headers)
    return verify_admin_session_token(token, get_settings())


def _extract_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _clamp_identifier(value: str) -> str:
    return value[:128]


def _extract_hint_from_payload(payload: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _extract_text(payload.get(key))
        if value:
            return _clamp_identifier(value)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in keys:
            value = _extract_text(metadata.get(key))
            if value:
                return _clamp_identifier(value)
    return None


def _resolve_session_id(request: Request, payload: dict[str, object]) -> str | None:
    header_session = _extract_text(request.headers.get("X-Session-Id"))
    if header_session:
        return _clamp_identifier(header_session)

    payload_session = _extract_hint_from_payload(payload, SESSION_HINT_KEYS)
    if payload_session:
        return payload_session

    user_value = _extract_text(payload.get("user"))
    if user_value:
        return _clamp_identifier(f"user:{user_value}")
    return None


def _resolve_trace_id(
    request: Request,
    payload: dict[str, object],
    session_id: str | None,
) -> str:
    header_trace = _extract_text(request.headers.get("X-Trace-Id"))
    if header_trace:
        return _clamp_identifier(header_trace)

    payload_trace = _extract_hint_from_payload(payload, TRACE_HINT_KEYS)
    if payload_trace:
        return payload_trace

    if session_id:
        return _clamp_identifier(session_id)
    return uuid.uuid4().hex


def _build_models_response(model_aliases: list[str]) -> dict[str, object]:
    models = [
        {"id": model_alias, "object": "model", "owned_by": "proxy"}
        for model_alias in model_aliases
    ]
    return {"object": "list", "data": models}


def _build_gemini_models_response(model_aliases: list[str]) -> dict[str, object]:
    models = [
        {
            "name": f"models/{model_alias}",
            "version": model_alias,
            "displayName": model_alias,
            "supportedGenerationMethods": [
                "generateContent",
                "streamGenerateContent",
                "countTokens",
            ],
        }
        for model_alias in model_aliases
    ]
    return {"models": models}


def _extract_gemini_model_alias(path: str) -> str | None:
    match = GEMINI_MODEL_PATH_PATTERN.search(path)
    if not match:
        return None
    model = unquote(match.group("model")).strip("/")
    if not model:
        return None
    return model


def _rewrite_gemini_model_path(path: str, real_model: str) -> str:
    match = GEMINI_MODEL_PATH_PATTERN.search(path)
    if not match:
        return path

    collection = match.group("collection")
    replacement = str(real_model or "").strip()
    if replacement.startswith(f"{collection}/"):
        replacement = replacement[len(collection) + 1 :]
    if not replacement:
        return path

    encoded_model = quote(replacement, safe="/")
    return (
        f"{path[:match.start('model')]}"
        f"{encoded_model}"
        f"{path[match.end('model'):]}"
    )


def _parse_json_object_bytes(content: bytes | None) -> dict[str, object] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _has_non_empty_value(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _contains_semantic_failure_marker(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple, set)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    normalized = text.strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in SEMANTIC_FAILURE_MARKERS)


def _has_success_signal(payload: dict[str, object]) -> bool:
    if str(payload.get("object") or "").strip().lower() == "error":
        return False
    return any(
        key in payload and _has_non_empty_value(payload.get(key))
        for key in SEMANTIC_SUCCESS_SIGNAL_KEYS
    )


def _semantic_failure_reason(
    content: bytes,
    content_type: str | None,
    payload: dict[str, object] | None,
    provider: str,
) -> str | None:
    stripped = content.strip()
    if not stripped:
        return "empty_response_body"

    normalized_content_type = str(content_type or "").lower()
    if payload is None:
        text_sample = stripped[:4096].decode("utf-8", errors="ignore").lower()
        if "text/html" in normalized_content_type or text_sample.startswith(
            ("<html", "<!doctype html")
        ):
            return "html_response_body"
        if _contains_semantic_failure_marker(text_sample):
            return "text_error_body"
        return None

    if _has_non_empty_value(payload.get("error")):
        return "error_field"
    if _has_non_empty_value(payload.get("errors")):
        return "errors_field"

    object_type = str(payload.get("object") or "").strip().lower()
    if object_type == "error":
        return "error_object"

    response_type = str(payload.get("type") or "").strip().lower()
    if response_type == "error":
        return "error_type"

    status = str(payload.get("status") or "").strip().lower()
    if status in SEMANTIC_FAILURE_STATUSES:
        return "failure_status"

    for flag_key in ("success", "ok"):
        if payload.get(flag_key) is False:
            return f"{flag_key}_false"

    for code_key in ("status_code", "code"):
        code_value = payload.get(code_key)
        if isinstance(code_value, int) and code_value >= 400:
            return f"{code_key}_failure"
        if isinstance(code_value, str) and _contains_semantic_failure_marker(code_value):
            return f"{code_key}_failure"

    if provider == "gemini" and not _has_non_empty_value(payload.get("candidates")):
        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and _has_non_empty_value(
            prompt_feedback.get("blockReason")
        ):
            return "gemini_blocked"

    if not _has_success_signal(payload):
        for message_key in ("message", "msg", "detail"):
            if _contains_semantic_failure_marker(payload.get(message_key)):
                return f"{message_key}_failure"

    return None


def _should_retry_same_candidate(status_code: int, attempt_index: int) -> bool:
    return (
        status_code in LOCAL_RETRY_STATUSES
        and attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS
    )


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _record_attempt_log(
    *,
    request_id: str,
    trace_id: str,
    model_alias: str,
    candidate: RouteCandidate,
    requested_rule_group: str | None,
    rule_group: str,
    attempt_order: int,
    status_code: int | None,
    outcome: str,
    failure_reason: str | None,
    latency_ms: int,
    agent_node: str | None,
    upstream_url: str,
) -> None:
    metrics = RequestAttemptMetrics(
        request_id=request_id,
        trace_id=trace_id,
        model_alias=model_alias,
        endpoint_id=candidate.endpoint.id,
        api_key_id=candidate.api_key.id,
        requested_rule_group=requested_rule_group,
        rule_group=rule_group,
        attempt_order=attempt_order,
        status_code=status_code,
        outcome=outcome,
        failure_reason=failure_reason,
        latency_ms=latency_ms,
        execution_mode=candidate.execution_mode,
        agent_node=agent_node,
        upstream_url=upstream_url,
    )
    safe_create_task(write_request_attempt_log(metrics))


async def _list_accessible_model_aliases(
    request: Request,
    session: AsyncSession,
    *,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
) -> list[str]:
    allowed_groups = await _resolve_allowed_rule_groups_from_token(session, request)
    provider_filters = normalize_provider_filters(provider_filter)

    result = await session.execute(
        select(ModelMap.model_alias, Endpoint.provider)
        .join(Endpoint, ModelMap.endpoint_id == Endpoint.id)
        .where(Endpoint.is_active.is_(True))
        .distinct()
    )
    rows = result.all()
    normalized_rows = [
        (str(model_alias), normalize_provider_name(provider))
        for model_alias, provider in rows
        if model_alias
    ]

    filtered_rows = (
        [row for row in normalized_rows if row[1] in provider_filters]
        if provider_filters
        else normalized_rows
    )
    if provider_filters and not filtered_rows and provider_filter_fallback_to_any:
        filtered_rows = normalized_rows

    model_aliases = sorted({model_alias for model_alias, _ in filtered_rows})
    if not model_aliases:
        return []

    # default 组拥有全量模型访问权限
    if any(group.lower() == "default" for group in allowed_groups):
        return model_aliases

    non_default_groups = [group for group in allowed_groups if group.lower() != "default"]
    if not non_default_groups:
        return []

    rule_result = await session.execute(
        select(RoutingRule)
        .where(
            RoutingRule.is_active.is_(True),
            RoutingRule.group_name.in_(non_default_groups),
        )
        .order_by(RoutingRule.priority.desc(), RoutingRule.id)
    )
    rules_by_group: dict[str, list[RoutingRule]] = {}
    for rule in rule_result.scalars().all():
        rules_by_group.setdefault(rule.group_name.lower(), []).append(rule)

    allowed_aliases: set[str] = set()
    for model_alias in model_aliases:
        for group in non_default_groups:
            matched = False
            for rule in rules_by_group.get(group.lower(), []):
                if model_pattern_matches(rule.model_pattern, model_alias):
                    matched = True
                    break
            if matched:
                allowed_aliases.add(model_alias)
                break

    return sorted(allowed_aliases)


async def list_models(
    request: Request,
    session: AsyncSession,
    *,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
) -> dict[str, object]:
    model_aliases = await _list_accessible_model_aliases(
        request,
        session,
        provider_filter=provider_filter,
        provider_filter_fallback_to_any=provider_filter_fallback_to_any,
    )
    return _build_models_response(model_aliases)


async def _proxy_openai_request(
    request: Request,
    session: AsyncSession,
    *,
    rewrite_model: bool = True,
    strip_rule_group_from_payload: bool = False,
    path_prefix: str | None = None,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
    allow_missing_model: bool = False,
    model_alias_override: str | None = None,
    target_path_rewriter: Callable[[str, RouteCandidate], str] | None = None,
) -> Response:
    raw_body = await request.body()
    payload: dict[str, object] = {}
    if raw_body:
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        payload = parsed

    model_alias_raw = payload.get("model")
    model_alias = model_alias_override
    if model_alias is None:
        model_alias = str(model_alias_raw) if model_alias_raw is not None else None
    header_model_alias = request.headers.get("X-Model-Alias")
    if not model_alias and header_model_alias:
        model_alias = str(header_model_alias)

    if rewrite_model and not model_alias and not allow_missing_model:
        raise HTTPException(status_code=400, detail="Missing model field")
    if not model_alias:
        model_alias = request.url.path

    payload_rule_group = payload.get("rule_group")
    if payload_rule_group is None:
        payload_rule_group = payload.get("rules")
    if not isinstance(payload_rule_group, str) or not payload_rule_group:
        payload_rule_group = request.headers.get("X-Rule-Group", "default")
    requested_rule_group = str(payload_rule_group or "default").strip() or "default"
    rule_group = await _resolve_rule_group_from_token(
        session, request, requested_rule_group
    )
    if strip_rule_group_from_payload:
        payload.pop("rule_group", None)
        payload.pop("rules", None)

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
    )

    if not candidates:
        raise HTTPException(status_code=404, detail="No available API keys")

    dump_rule = await _find_dump_rule(session, model_alias, effective_group)

    request_id = uuid.uuid4().hex
    session_id = _resolve_session_id(request, payload)
    trace_id = _resolve_trace_id(request, payload, session_id)
    request_start = time.perf_counter()
    include_internal_debug = _include_debug_headers(request)
    client = await get_http_client()
    attempt_order = 0

    async def reserve_candidate_or_raise(
        candidate: RouteCandidate,
        attempt_order: int,
        attempt_start: float,
        agent_node: str | None,
        upstream_url: str,
    ) -> bool:
        if await router_service.reserve_candidate_attempt(candidate):
            return True
        _record_attempt_log(
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            candidate=candidate,
            requested_rule_group=requested_rule_group,
            rule_group=effective_group,
            attempt_order=attempt_order,
            status_code=None,
            outcome="fallback" if candidate != candidates[-1] else "error",
            failure_reason="rpm_limit",
            latency_ms=_elapsed_ms(attempt_start),
            agent_node=agent_node,
            upstream_url=upstream_url,
        )
        if candidate != candidates[-1]:
            return False
        raise HTTPException(status_code=429, detail="API key rate limit exceeded")

    for candidate in candidates:
        upstream_payload = payload
        should_rewrite_body_model = (
            rewrite_model
            and "model" in payload
            and payload.get("model") != candidate.real_model
        )
        if should_rewrite_body_model:
            upstream_payload = dict(payload)
            upstream_payload["model"] = candidate.real_model

        templated_payload = _apply_request_body_template(
            candidate.endpoint, upstream_payload, candidate.real_model
        )
        if templated_payload is not None:
            upstream_payload = templated_payload

        if templated_payload is not None or should_rewrite_body_model:
            upstream_body = json.dumps(upstream_payload).encode("utf-8")
        else:
            upstream_body = raw_body

        headers = _build_upstream_headers(
            request.headers, candidate.endpoint, candidate.api_key.key
        )
        oauth_enabled = False
        try:
            headers, oauth_enabled = await _apply_oauth_access_token(
                headers,
                candidate.endpoint,
                redis,
                client,
            )
        except Exception as exc:
            await circuit_breaker.record_failure(candidate.api_key.id)
            if candidate != candidates[-1]:
                continue
            raise HTTPException(status_code=502, detail="OAuth token refresh failed") from exc

        if not any(key.lower() == "x-trace-id" for key in headers):
            headers["X-Trace-Id"] = trace_id
        target_path = (
            target_path_rewriter(request.url.path, candidate)
            if target_path_rewriter is not None
            else None
        )
        url = _build_target_url(
            candidate.endpoint.base_url,
            request,
            path_prefix=path_prefix,
            endpoint=candidate.endpoint,
            path_override=target_path,
        )
        accept_header = request.headers.get("accept", "").lower()
        is_stream = bool(upstream_payload.get("stream")) or "text/event-stream" in accept_header
        debug_headers = _build_debug_headers(
            request_id,
            trace_id,
            candidate,
            model_alias,
            include_internal=include_internal_debug,
        )
        agent_name = _get_agent_name(candidate.endpoint)
        candidate_provider = normalize_provider_name(
            getattr(candidate.endpoint, "provider", None)
        )

        if agent_name:
            agent_manager = get_agent_manager()
            for attempt_index in range(UPSTREAM_CANDIDATE_MAX_ATTEMPTS):
                attempt_order += 1
                attempt_start = time.perf_counter()
                if not await reserve_candidate_or_raise(
                    candidate,
                    attempt_order,
                    attempt_start,
                    agent_name,
                    url,
                ):
                    break
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
                    _record_attempt_log(
                        request_id=request_id,
                        trace_id=trace_id,
                        model_alias=model_alias,
                        candidate=candidate,
                        requested_rule_group=requested_rule_group,
                        rule_group=effective_group,
                        attempt_order=attempt_order,
                        status_code=None,
                        outcome="retry"
                        if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS
                        else ("fallback" if candidate != candidates[-1] else "error"),
                        failure_reason="agent_unavailable",
                        latency_ms=_elapsed_ms(attempt_start),
                        agent_node=agent_name,
                        upstream_url=url,
                    )
                    await circuit_breaker.record_failure(candidate.api_key.id)
                    if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS:
                        continue
                    if candidate != candidates[-1]:
                        break
                    raise HTTPException(status_code=502, detail="Agent unavailable")

                status_code = agent_response.status_code or 500

                if status_code == 401 and oauth_enabled:
                    try:
                        headers, _ = await _apply_oauth_access_token(
                            headers,
                            candidate.endpoint,
                            redis,
                            client,
                            force_refresh=True,
                        )
                        retry_request = AgentRequest(
                            method=request.method,
                            url=url,
                            headers=headers,
                            body=upstream_body,
                            stream=is_stream,
                        )
                        agent_response = await agent_manager.send_request(
                            agent_name, retry_request
                        )
                        status_code = agent_response.status_code or 500
                    except AgentUnavailableError:
                        await circuit_breaker.record_failure(candidate.api_key.id)
                        if candidate != candidates[-1]:
                            break
                        raise HTTPException(status_code=502, detail="Agent unavailable")
                    except Exception as exc:
                        await circuit_breaker.record_failure(candidate.api_key.id)
                        if candidate != candidates[-1]:
                            break
                        raise HTTPException(
                            status_code=502, detail="OAuth token refresh failed"
                        ) from exc

                if status_code in CANDIDATE_FALLBACK_STATUSES:
                    if status_code in CIRCUIT_BREAKER_STATUSES:
                        await circuit_breaker.record_failure(candidate.api_key.id)
                    if is_stream:
                        content = await agent_response.read_all()
                    else:
                        content = agent_response.body
                    should_retry = _should_retry_same_candidate(status_code, attempt_index)
                    _record_attempt_log(
                        request_id=request_id,
                        trace_id=trace_id,
                        model_alias=model_alias,
                        candidate=candidate,
                        requested_rule_group=requested_rule_group,
                        rule_group=effective_group,
                        attempt_order=attempt_order,
                        status_code=status_code,
                        outcome="retry"
                        if should_retry
                        else ("fallback" if candidate != candidates[-1] else "returned"),
                        failure_reason=f"http_{status_code}",
                        latency_ms=_elapsed_ms(attempt_start),
                        agent_node=agent_name,
                        upstream_url=url,
                    )
                    if should_retry:
                        continue
                    if candidate != candidates[-1]:
                        break
                    safe_create_task(
                        _dump_proxy_record(
                            dump_rule,
                            request_id,
                            trace_id,
                            candidate.endpoint.name,
                            model_alias,
                            upstream_body,
                            content,
                            status_code,
                            session_id=session_id,
                            request_path=request.url.path,
                        )
                    )
                    return Response(
                        content=content,
                        status_code=status_code,
                        media_type=agent_response.headers.get("content-type"),
                        headers=_merge_headers(
                            _filter_response_headers(agent_response.headers), debug_headers
                        ),
                    )

                if is_stream:
                    await circuit_breaker.record_success(candidate.api_key.id)
                    await router_service.record_candidate_success(candidate)
                    _record_attempt_log(
                        request_id=request_id,
                        trace_id=trace_id,
                        model_alias=model_alias,
                        candidate=candidate,
                        requested_rule_group=requested_rule_group,
                        rule_group=effective_group,
                        attempt_order=attempt_order,
                        status_code=status_code,
                        outcome="success",
                        failure_reason=None,
                        latency_ms=_elapsed_ms(attempt_start),
                        agent_node=agent_name,
                        upstream_url=url,
                    )
                    stream_headers = _merge_headers(
                        _filter_response_headers(agent_response.headers), debug_headers
                    )

                    async def agent_stream_generator() -> AsyncGenerator[bytes, None]:
                        buffer = ""
                        usage_payload = None
                        first_data_at: float | None = None
                        chunks: list[bytes] = []
                        async for chunk in agent_response.iter_bytes():
                            if chunk:
                                chunks.append(chunk)
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
                            requested_rule_group=requested_rule_group,
                            rule_group=effective_group,
                            status_code=status_code,
                            latency_ms=latency_ms,
                            ttft_ms=ttft_ms,
                            tps=tps,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            execution_mode=candidate.execution_mode,
                            agent_node=agent_name,
                            upstream_url=url,
                        )
                        safe_create_task(write_request_log(metrics))
                        safe_create_task(
                            _dump_proxy_record(
                                dump_rule,
                                request_id,
                                trace_id,
                                candidate.endpoint.name,
                                model_alias,
                                upstream_body,
                                b"".join(chunks),
                                status_code,
                                session_id=session_id,
                                request_path=request.url.path,
                            )
                        )

                    return StreamingResponse(
                        agent_stream_generator(),
                        status_code=status_code,
                        media_type=agent_response.headers.get("content-type"),
                        headers=stream_headers,
                    )

                latency_ms = int((time.perf_counter() - request_start) * 1000)
                response_payload = _parse_json_object_bytes(agent_response.body)
                semantic_failure_reason = _semantic_failure_reason(
                    agent_response.body,
                    agent_response.headers.get("content-type"),
                    response_payload,
                    candidate_provider,
                )
                if semantic_failure_reason:
                    await circuit_breaker.record_failure(candidate.api_key.id)
                    _record_attempt_log(
                        request_id=request_id,
                        trace_id=trace_id,
                        model_alias=model_alias,
                        candidate=candidate,
                        requested_rule_group=requested_rule_group,
                        rule_group=effective_group,
                        attempt_order=attempt_order,
                        status_code=status_code,
                        outcome="fallback" if candidate != candidates[-1] else "returned",
                        failure_reason=f"semantic_{semantic_failure_reason}",
                        latency_ms=_elapsed_ms(attempt_start),
                        agent_node=agent_name,
                        upstream_url=url,
                    )
                    if candidate != candidates[-1]:
                        break
                    safe_create_task(
                        _dump_proxy_record(
                            dump_rule,
                            request_id,
                            trace_id,
                            candidate.endpoint.name,
                            model_alias,
                            upstream_body,
                            agent_response.body,
                            status_code,
                            session_id=session_id,
                            request_path=request.url.path,
                        )
                    )
                    return Response(
                        content=agent_response.body,
                        status_code=status_code,
                        media_type=agent_response.headers.get("content-type"),
                        headers=_merge_headers(
                            _filter_response_headers(agent_response.headers), debug_headers
                        ),
                    )

                await circuit_breaker.record_success(candidate.api_key.id)
                await router_service.record_candidate_success(candidate)
                _record_attempt_log(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    candidate=candidate,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    attempt_order=attempt_order,
                    status_code=status_code,
                    outcome="success",
                    failure_reason=None,
                    latency_ms=_elapsed_ms(attempt_start),
                    agent_node=agent_name,
                    upstream_url=url,
                )

                prompt_tokens, completion_tokens, total_tokens = extract_usage(response_payload)
                metrics = RequestMetrics(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    endpoint_id=candidate.endpoint.id,
                    api_key_id=candidate.api_key.id,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    ttft_ms=None,
                    tps=None,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    execution_mode=candidate.execution_mode,
                    agent_node=agent_name,
                    upstream_url=url,
                )
                safe_create_task(write_request_log(metrics))

                safe_create_task(
                    _dump_proxy_record(
                        dump_rule,
                        request_id,
                        trace_id,
                        candidate.endpoint.name,
                        model_alias,
                        upstream_body,
                        agent_response.body,
                        status_code,
                        session_id=session_id,
                        request_path=request.url.path,
                    )
                )

                return Response(
                    content=agent_response.body,
                    status_code=status_code,
                    media_type=agent_response.headers.get("content-type"),
                    headers=_merge_headers(
                        _filter_response_headers(agent_response.headers), debug_headers
                    ),
                )

            continue

        for attempt_index in range(UPSTREAM_CANDIDATE_MAX_ATTEMPTS):
            attempt_order += 1
            attempt_start = time.perf_counter()
            if not await reserve_candidate_or_raise(
                candidate,
                attempt_order,
                attempt_start,
                agent_name,
                url,
            ):
                break
            try:
                request_obj = client.build_request(
                    request.method,
                    url,
                    headers=headers,
                    content=upstream_body,
                )
                response = await client.send(request_obj, stream=is_stream)
            except Exception as exc:
                _record_attempt_log(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    candidate=candidate,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    attempt_order=attempt_order,
                    status_code=None,
                    outcome="retry"
                    if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS
                    else ("fallback" if candidate != candidates[-1] else "error"),
                    failure_reason="connection_error",
                    latency_ms=_elapsed_ms(attempt_start),
                    agent_node=agent_name,
                    upstream_url=url,
                )
                await circuit_breaker.record_failure(candidate.api_key.id)
                if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS:
                    continue
                if candidate != candidates[-1]:
                    break
                raise HTTPException(
                    status_code=502,
                    detail="Upstream connection error",
                ) from exc

            if response.status_code == 401 and oauth_enabled:
                await response.aclose()
                try:
                    headers, _ = await _apply_oauth_access_token(
                        headers,
                        candidate.endpoint,
                        redis,
                        client,
                        force_refresh=True,
                    )
                except Exception as exc:
                    await circuit_breaker.record_failure(candidate.api_key.id)
                    if candidate != candidates[-1]:
                        break
                    raise HTTPException(
                        status_code=502,
                        detail="OAuth token refresh failed",
                    ) from exc
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
                    if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS:
                        continue
                    if candidate != candidates[-1]:
                        break
                    raise HTTPException(
                        status_code=502,
                        detail="Upstream connection error",
                    ) from exc

            if response.status_code in CANDIDATE_FALLBACK_STATUSES:
                if response.status_code in CIRCUIT_BREAKER_STATUSES:
                    await circuit_breaker.record_failure(candidate.api_key.id)
                content = await response.aread()
                await response.aclose()
                should_retry = _should_retry_same_candidate(response.status_code, attempt_index)
                _record_attempt_log(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    candidate=candidate,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    attempt_order=attempt_order,
                    status_code=response.status_code,
                    outcome="retry"
                    if should_retry
                    else ("fallback" if candidate != candidates[-1] else "returned"),
                    failure_reason=f"http_{response.status_code}",
                    latency_ms=_elapsed_ms(attempt_start),
                    agent_node=agent_name,
                    upstream_url=url,
                )
                if should_retry:
                    continue
                if candidate != candidates[-1]:
                    break
                safe_create_task(
                    _dump_proxy_record(
                        dump_rule,
                        request_id,
                        trace_id,
                        candidate.endpoint.name,
                        model_alias,
                        upstream_body,
                        content,
                        response.status_code,
                        session_id=session_id,
                        request_path=request.url.path,
                    )
                )
                return Response(
                    content=content,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type"),
                    headers=_merge_headers(
                        _filter_response_headers(response.headers), debug_headers
                    ),
                )

            latency_ms = int((time.perf_counter() - request_start) * 1000)

            if is_stream:
                await circuit_breaker.record_success(candidate.api_key.id)
                await router_service.record_candidate_success(candidate)
                _record_attempt_log(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    candidate=candidate,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    attempt_order=attempt_order,
                    status_code=response.status_code,
                    outcome="success",
                    failure_reason=None,
                    latency_ms=_elapsed_ms(attempt_start),
                    agent_node=agent_name,
                    upstream_url=url,
                )
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
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    request_start=request_start,
                    dump_rule=dump_rule,
                    dump_endpoint_name=candidate.endpoint.name,
                    dump_request_body=upstream_body,
                    dump_session_id=session_id,
                    dump_request_path=request.url.path,
                    execution_mode=candidate.execution_mode,
                    agent_node=agent_name,
                    upstream_url=url,
                )
                return StreamingResponse(
                    generator,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type"),
                    headers=stream_headers,
                )

            content = await response.aread()
            response_payload = _parse_json_object_bytes(content)
            semantic_failure_reason = _semantic_failure_reason(
                content,
                response.headers.get("content-type"),
                response_payload,
                candidate_provider,
            )
            if semantic_failure_reason:
                await circuit_breaker.record_failure(candidate.api_key.id)
                await response.aclose()
                _record_attempt_log(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    candidate=candidate,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    attempt_order=attempt_order,
                    status_code=response.status_code,
                    outcome="fallback" if candidate != candidates[-1] else "returned",
                    failure_reason=f"semantic_{semantic_failure_reason}",
                    latency_ms=_elapsed_ms(attempt_start),
                    agent_node=agent_name,
                    upstream_url=url,
                )
                if candidate != candidates[-1]:
                    break
                safe_create_task(
                    _dump_proxy_record(
                        dump_rule,
                        request_id,
                        trace_id,
                        candidate.endpoint.name,
                        model_alias,
                        upstream_body,
                        content,
                        response.status_code,
                        session_id=session_id,
                        request_path=request.url.path,
                    )
                )
                return Response(
                    content=content,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type"),
                    headers=_merge_headers(
                        _filter_response_headers(response.headers), debug_headers
                    ),
                )

            await circuit_breaker.record_success(candidate.api_key.id)
            await router_service.record_candidate_success(candidate)
            _record_attempt_log(
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                candidate=candidate,
                requested_rule_group=requested_rule_group,
                rule_group=effective_group,
                attempt_order=attempt_order,
                status_code=response.status_code,
                outcome="success",
                failure_reason=None,
                latency_ms=_elapsed_ms(attempt_start),
                agent_node=agent_name,
                upstream_url=url,
            )

            prompt_tokens, completion_tokens, total_tokens = extract_usage(response_payload)
            metrics = RequestMetrics(
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                endpoint_id=candidate.endpoint.id,
                api_key_id=candidate.api_key.id,
                requested_rule_group=requested_rule_group,
                rule_group=effective_group,
                status_code=response.status_code,
                latency_ms=latency_ms,
                ttft_ms=None,
                tps=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                execution_mode=candidate.execution_mode,
                agent_node=agent_name,
                upstream_url=url,
            )
            safe_create_task(write_request_log(metrics))

            safe_create_task(
                _dump_proxy_record(
                    dump_rule,
                    request_id,
                    trace_id,
                    candidate.endpoint.name,
                    model_alias,
                    upstream_body,
                    content,
                    response.status_code,
                    session_id=session_id,
                    request_path=request.url.path,
                )
            )

            return Response(
                content=content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type"),
                headers=_merge_headers(
                    _filter_response_headers(response.headers), debug_headers
                ),
            )

        continue

    raise HTTPException(status_code=502, detail="All upstream requests failed")


async def chat_completions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(request, session)


async def completions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(request, session)


async def embeddings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(request, session)


async def responses(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=False,
        strip_rule_group_from_payload=False,
    )


async def openai_passthrough(
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_path = path.strip("/")
    if request.method.upper() == "GET" and normalized_path == "models":
        try:
            payload = await list_models(
                request,
                session,
                provider_filter=("openai", "custom"),
                provider_filter_fallback_to_any=True,
            )
        except (AttributeError, AssertionError):
            payload = None
        if payload is not None:
            return JSONResponse(content=payload)

    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=True,
        strip_rule_group_from_payload=False,
        path_prefix="/openai",
        provider_filter=("openai", "custom"),
        provider_filter_fallback_to_any=True,
        allow_missing_model=True,
    )


async def anthropic_passthrough(
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    _ = path
    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=True,
        strip_rule_group_from_payload=False,
        path_prefix="/anthropic",
        provider_filter=("anthropic", "custom"),
        provider_filter_fallback_to_any=True,
        allow_missing_model=True,
    )


async def gemini_passthrough(
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_path = path.strip("/")
    if request.method.upper() == "GET" and normalized_path == "models":
        try:
            model_aliases = await _list_accessible_model_aliases(
                request,
                session,
                provider_filter=("gemini", "custom"),
                provider_filter_fallback_to_any=True,
            )
        except (AttributeError, AssertionError):
            model_aliases = None
        if model_aliases is not None:
            payload = _build_gemini_models_response(model_aliases)
            return JSONResponse(content=payload)

    model_alias = _extract_gemini_model_alias(request.url.path)
    if model_alias is None:
        model_alias = request.headers.get("X-Model-Alias")

    return await _proxy_openai_request(
        request,
        session,
        rewrite_model=False,
        strip_rule_group_from_payload=False,
        path_prefix="/gemini",
        provider_filter=("gemini", "custom"),
        provider_filter_fallback_to_any=True,
        allow_missing_model=False,
        model_alias_override=model_alias,
        target_path_rewriter=lambda raw_path, candidate: _rewrite_gemini_model_path(
            raw_path,
            candidate.real_model,
        ),
    )

from typing import AsyncGenerator
import asyncio
import json
import time
import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import _dump_proxy_record, _find_dump_rule, _resolve_rule_group_from_token
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
from app.core.http_client import get_http_client
from app.core.redis import get_redis
from app.db.models import APIKey, Endpoint, ModelMap
from app.db.session import get_session
from app.services.agent_transport import AgentRequest, AgentUnavailableError, get_agent_manager
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.circuit_breaker import CircuitBreaker
from app.services.notifications import get_notifier
from app.services.router import ModelRouter, RouteCandidate

RETRYABLE_STATUSES = {401, 429, 500, 502, 503, 504}
CIRCUIT_BREAKER_STATUSES = {401, 429}
SESSION_HINT_KEYS = (
    "session_id",
    "conversation_id",
    "thread_id",
    "chat_id",
    "dialog_id",
    "previous_response_id",
)
TRACE_HINT_KEYS = ("trace_id", "request_id")


def _normalize_provider_name(value: object) -> str:
    if not isinstance(value, str):
        return "openai"
    normalized = value.strip().lower()
    return normalized or "openai"


def _normalize_provider_filters(
    provider_filter: str | tuple[str, ...] | None,
) -> set[str] | None:
    if provider_filter is None:
        return None
    if isinstance(provider_filter, str):
        return {_normalize_provider_name(provider_filter)}
    filters = {
        _normalize_provider_name(provider)
        for provider in provider_filter
        if isinstance(provider, str) and provider.strip()
    }
    return filters or None


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


async def list_models(session: AsyncSession = Depends(get_session)) -> dict:
    result = await session.execute(select(ModelMap.model_alias).distinct())
    models = [
        {"id": model_alias, "object": "model", "owned_by": "proxy"}
        for (model_alias,) in result.all()
    ]
    return {"object": "list", "data": models}


async def _proxy_openai_request(
    request: Request,
    session: AsyncSession,
    *,
    rewrite_model: bool = True,
    strip_rule_group_from_payload: bool = True,
    path_prefix: str | None = None,
    provider_filter: str | tuple[str, ...] | None = None,
    provider_filter_fallback_to_any: bool = False,
    allow_missing_model: bool = False,
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
    model_alias = str(model_alias_raw) if model_alias_raw is not None else None
    header_model_alias = request.headers.get("X-Model-Alias")
    if not model_alias and header_model_alias:
        model_alias = str(header_model_alias)

    has_explicit_model = model_alias_raw is not None or bool(header_model_alias)

    if rewrite_model and not model_alias and not allow_missing_model:
        raise HTTPException(status_code=400, detail="Missing model field")
    if not model_alias:
        model_alias = request.url.path

    payload_rule_group = payload.get("rule_group")
    if payload_rule_group is None:
        payload_rule_group = payload.get("rules")
    if not isinstance(payload_rule_group, str) or not payload_rule_group:
        payload_rule_group = request.headers.get("X-Rule-Group", "default")
    rule_group = await _resolve_rule_group_from_token(
        session, request, payload_rule_group
    )
    if strip_rule_group_from_payload:
        payload.pop("rule_group", None)
        payload.pop("rules", None)

    redis = await get_redis()
    notifier = get_notifier()
    circuit_breaker = CircuitBreaker(redis, notifier=notifier)
    router_service = ModelRouter(circuit_breaker)

    all_candidates, effective_group = await router_service.get_candidates(
        session, model_alias, rule_group
    )
    candidates = all_candidates
    provider_filters = _normalize_provider_filters(provider_filter)
    if provider_filters:
        provider_candidates = [
            candidate
            for candidate in all_candidates
            if _normalize_provider_name(candidate.endpoint.provider) in provider_filters
        ]
        if provider_candidates:
            candidates = provider_candidates
        elif not provider_filter_fallback_to_any:
            candidates = []

    if not candidates:
        fallback_stmt = (
            select(APIKey, Endpoint)
            .join(Endpoint, APIKey.endpoint_id == Endpoint.id)
            .where(
                APIKey.is_active.is_(True),
                Endpoint.is_active.is_(True),
                APIKey.rule_group == effective_group,
            )
            .order_by(APIKey.id)
        )

        fallback_rows = []
        if provider_filters:
            provider_result = await session.execute(fallback_stmt)
            fallback_rows = [
                row
                for row in provider_result.all()
                if _normalize_provider_name(row[1].provider) in provider_filters
            ]
            if not fallback_rows and provider_filter_fallback_to_any:
                any_result = await session.execute(fallback_stmt)
                fallback_rows = any_result.all()
        else:
            fallback_result = await session.execute(fallback_stmt)
            fallback_rows = fallback_result.all()

        for api_key, endpoint in fallback_rows:
            if not await router_service._is_key_available(api_key):
                continue
            candidates.append(
                RouteCandidate(
                    api_key=api_key,
                    endpoint=endpoint,
                    real_model=model_alias,
                )
            )

    if not candidates:
        raise HTTPException(status_code=404, detail="No available API keys")

    dump_rule = await _find_dump_rule(session, model_alias, effective_group)

    request_id = uuid.uuid4().hex
    session_id = _resolve_session_id(request, payload)
    trace_id = _resolve_trace_id(request, payload, session_id)
    request_start = time.perf_counter()
    client = await get_http_client()

    for candidate in candidates:
        upstream_payload = dict(payload)
        if rewrite_model and has_explicit_model:
            upstream_payload["model"] = candidate.real_model

        # 应用请求体模板（如果配置）
        templated_payload = _apply_request_body_template(
            candidate.endpoint, upstream_payload, candidate.real_model
        )
        if templated_payload is not None:
            upstream_payload = templated_payload

        if raw_body or has_explicit_model or templated_payload is not None:
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
        url = _build_target_url(
            candidate.endpoint.base_url, request, path_prefix=path_prefix, endpoint=candidate.endpoint
        )
        accept_header = request.headers.get("accept", "").lower()
        is_stream = bool(upstream_payload.get("stream")) or "text/event-stream" in accept_header
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
                    if candidate != candidates[-1]:
                        continue
                    raise HTTPException(status_code=502, detail="Agent unavailable")
                except Exception as exc:
                    await circuit_breaker.record_failure(candidate.api_key.id)
                    if candidate != candidates[-1]:
                        continue
                    raise HTTPException(
                        status_code=502, detail="OAuth token refresh failed"
                    ) from exc

            if status_code in RETRYABLE_STATUSES:
                if status_code in CIRCUIT_BREAKER_STATUSES:
                    await circuit_breaker.record_failure(candidate.api_key.id)
                if is_stream:
                    content = await agent_response.read_all()
                else:
                    content = agent_response.body
                if candidate != candidates[-1]:
                    continue
                asyncio.create_task(
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

            await circuit_breaker.record_success(candidate.api_key.id)

            if is_stream:
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
                    asyncio.create_task(
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

            asyncio.create_task(
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
                    continue
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
                if candidate != candidates[-1]:
                    continue
                raise HTTPException(
                    status_code=502,
                    detail="Upstream connection error",
                ) from exc

        if response.status_code in RETRYABLE_STATUSES:
            if response.status_code in CIRCUIT_BREAKER_STATUSES:
                await circuit_breaker.record_failure(candidate.api_key.id)
            content = await response.aread()
            await response.aclose()
            if candidate != candidates[-1]:
                continue
            asyncio.create_task(
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
                dump_rule=dump_rule,
                dump_endpoint_name=candidate.endpoint.name,
                dump_request_body=upstream_body,
                dump_session_id=session_id,
                dump_request_path=request.url.path,
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

        asyncio.create_task(
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
    _ = path
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

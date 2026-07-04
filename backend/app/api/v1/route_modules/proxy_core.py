from typing import Callable
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_helpers import (
    _dump_proxy_record,
    _find_dump_rule,
    _resolve_rule_group_from_token,
)
from app.api.v1.route_modules.proxy_agent_streams import (
    agent_stream_generator as _agent_stream_generator,
)
from app.api.v1.route_modules.proxy_attempts import (
    elapsed_ms as _elapsed_ms,
    record_attempt_log as _record_attempt_log,
    reserve_candidate_attempt_or_raise as _reserve_candidate_attempt_or_raise,
)
from app.api.v1.route_modules.proxy_failures import (
    CANDIDATE_FALLBACK_STATUSES,
    CIRCUIT_BREAKER_STATUSES,
    UPSTREAM_CANDIDATE_MAX_ATTEMPTS,
    parse_json_object_bytes,
    semantic_failure_reason as detect_semantic_failure_reason,
    should_retry_same_candidate,
)
from app.api.v1.route_modules.proxy_payloads import (
    extract_requested_rule_group,
    is_stream_request,
    parse_request_payload,
    prepare_upstream_payload_and_body,
    resolve_model_alias,
)
from app.api.v1.route_modules.proxy_trace import (
    include_debug_headers,
    resolve_session_id,
    resolve_trace_id,
)
from app.api.v1.route_proxy_helpers import (
    _apply_oauth_access_token,
    _build_debug_headers,
    _build_target_url,
    _build_upstream_headers,
    _filter_response_headers,
    _get_agent_name,
    _merge_headers,
    _stream_response,
)
from app.core.http_client import get_http_client
from app.core.providers import normalize_provider_name
from app.core.redis import get_redis
from app.services.agent_transport import (
    AgentRequest,
    AgentUnavailableError,
    get_agent_manager,
)
from app.services.background_tasks import safe_create_task
from app.services.billing import (
    RequestMetrics,
    extract_usage,
    write_request_log,
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
    allow_missing_model: bool = False,
    model_alias_override: str | None = None,
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
    )
    requested_rule_group = extract_requested_rule_group(request, payload)
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
    session_id = resolve_session_id(request, payload)
    trace_id = resolve_trace_id(request, payload, session_id)
    request_start = time.perf_counter()
    include_internal_debug = include_debug_headers(request)
    client = await get_http_client()
    attempt_order = 0

    for candidate in candidates:
        upstream_payload, upstream_body = prepare_upstream_payload_and_body(
            payload,
            raw_body,
            candidate,
            rewrite_model=rewrite_model,
        )

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
        is_stream = is_stream_request(request, upstream_payload)
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
                if not await _reserve_candidate_attempt_or_raise(
                    router_service=router_service,
                    candidate=candidate,
                    last_candidate=candidates[-1],
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    requested_rule_group=requested_rule_group,
                    effective_group=effective_group,
                    attempt_order=attempt_order,
                    attempt_start=attempt_start,
                    agent_node=agent_name,
                    upstream_url=url,
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
                    should_retry = should_retry_same_candidate(status_code, attempt_index)
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
                    generator = _agent_stream_generator(
                        agent_response=agent_response,
                        request_start=request_start,
                        request_id=request_id,
                        trace_id=trace_id,
                        model_alias=model_alias,
                        candidate=candidate,
                        requested_rule_group=requested_rule_group,
                        effective_group=effective_group,
                        status_code=status_code,
                        agent_name=agent_name,
                        upstream_url=url,
                        dump_rule=dump_rule,
                        upstream_body=upstream_body,
                        session_id=session_id,
                        request_path=request.url.path,
                    )

                    return StreamingResponse(
                        generator,
                        status_code=status_code,
                        media_type=agent_response.headers.get("content-type"),
                        headers=stream_headers,
                    )

                latency_ms = int((time.perf_counter() - request_start) * 1000)
                response_payload = parse_json_object_bytes(agent_response.body)
                semantic_failure_reason = detect_semantic_failure_reason(
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
            if not await _reserve_candidate_attempt_or_raise(
                router_service=router_service,
                candidate=candidate,
                last_candidate=candidates[-1],
                request_id=request_id,
                trace_id=trace_id,
                model_alias=model_alias,
                requested_rule_group=requested_rule_group,
                effective_group=effective_group,
                attempt_order=attempt_order,
                attempt_start=attempt_start,
                agent_node=agent_name,
                upstream_url=url,
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
                should_retry = should_retry_same_candidate(response.status_code, attempt_index)
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
            response_payload = parse_json_object_bytes(content)
            semantic_failure_reason = detect_semantic_failure_reason(
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


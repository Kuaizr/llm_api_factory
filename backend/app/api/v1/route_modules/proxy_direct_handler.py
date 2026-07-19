from dataclasses import dataclass
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.api.v1.route_modules.proxy_attempts import (
    elapsed_ms as _elapsed_ms,
    record_attempt_log as _write_attempt_log,
    reserve_candidate_attempt_or_raise as _reserve_candidate_attempt_or_raise,
)
from app.api.v1.route_modules.proxy_context import CandidateRequestContext
from app.api.v1.route_modules.proxy_failures import (
    CANDIDATE_FALLBACK_STATUSES,
    CIRCUIT_BREAKER_STATUSES,
    UPSTREAM_CANDIDATE_MAX_ATTEMPTS,
    parse_json_object_bytes,
    semantic_failure_reason as detect_semantic_failure_reason,
    should_retry_same_candidate,
)
from app.api.v1.route_modules.proxy_responses import raw_proxy_response_with_dump
from app.api.v1.route_proxy_helpers import (
    _apply_oauth_access_token,
    _filter_response_headers,
    _merge_headers,
    _stream_response,
)
from app.services.background_tasks import safe_create_task
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.circuit_breaker import CircuitBreaker
from app.services.codex_usage import record_codex_usage_from_headers
from app.services.codex_oauth import apply_codex_auth_headers, resolve_codex_credential
from app.db.session import SessionLocal
from app.services.router import ModelRouter, RouteCandidate


@dataclass(frozen=True)
class CandidateProxyResult:
    response: Response | None
    attempt_order: int


async def handle_direct_candidate(
    *,
    request: Request,
    candidate: RouteCandidate,
    last_candidate: RouteCandidate,
    candidate_context: CandidateRequestContext,
    router_service: ModelRouter,
    circuit_breaker: CircuitBreaker,
    client,
    redis,
    request_id: str,
    trace_id: str,
    model_alias: str,
    requested_rule_group: str | None,
    effective_group: str,
    exposure_format: str,
    dump_rule,
    session_id: str | None,
    request_start: float,
    attempt_order: int,
) -> CandidateProxyResult:
    upstream_body = candidate_context.upstream_body
    headers = candidate_context.headers
    oauth_enabled = candidate_context.oauth_enabled
    url = candidate_context.url
    is_stream = candidate_context.is_stream
    debug_headers = candidate_context.debug_headers
    agent_name = candidate_context.agent_name
    candidate_provider = candidate_context.candidate_provider

    def _record_attempt_log(**kwargs) -> None:  # noqa: ANN003
        _write_attempt_log(exposure_format=exposure_format, **kwargs)

    for attempt_index in range(UPSTREAM_CANDIDATE_MAX_ATTEMPTS):
        attempt_order += 1
        attempt_start = time.perf_counter()
        if not await _reserve_candidate_attempt_or_raise(
            router_service=router_service,
            candidate=candidate,
            last_candidate=last_candidate,
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            requested_rule_group=requested_rule_group,
            effective_group=effective_group,
            attempt_order=attempt_order,
            attempt_start=attempt_start,
            agent_node=agent_name,
            upstream_url=url,
            exposure_format=exposure_format,
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
                else ("fallback" if candidate != last_candidate else "error"),
                failure_reason="connection_error",
                latency_ms=_elapsed_ms(attempt_start),
                agent_node=agent_name,
                upstream_url=url,
            )
            await circuit_breaker.record_failure(candidate.api_key.id)
            if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS:
                continue
            if candidate != last_candidate:
                break
            raise HTTPException(
                status_code=502,
                detail="Upstream connection error",
            ) from exc

        if response.status_code == 401 and candidate_provider == "codex":
            await response.aclose()
            try:
                credential = await resolve_codex_credential(
                    candidate.api_key,
                    client=client,
                    session_factory=SessionLocal,
                    redis=redis,
                    force_refresh=True,
                    endpoint=candidate.endpoint,
                )
                headers = apply_codex_auth_headers(headers, credential)
                request_obj = client.build_request(
                    request.method,
                    url,
                    headers=headers,
                    content=upstream_body,
                )
                response = await client.send(request_obj, stream=is_stream)
            except Exception as exc:
                await circuit_breaker.record_failure(candidate.api_key.id)
                if candidate != last_candidate:
                    break
                raise HTTPException(
                    status_code=502,
                    detail="Codex token refresh failed",
                ) from exc
        elif response.status_code == 401 and oauth_enabled:
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
                if candidate != last_candidate:
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
                if candidate != last_candidate:
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
                else ("fallback" if candidate != last_candidate else "returned"),
                failure_reason=f"http_{response.status_code}",
                latency_ms=_elapsed_ms(attempt_start),
                agent_node=agent_name,
                upstream_url=url,
            )
            if should_retry:
                continue
            if candidate != last_candidate:
                break
            return CandidateProxyResult(
                response=raw_proxy_response_with_dump(
                    dump_rule,
                    content=content,
                    request_id=request_id,
                    trace_id=trace_id,
                    endpoint_name=candidate.endpoint.name,
                    endpoint_id=candidate.endpoint.id,
                    model_alias=model_alias,
                    real_model=candidate.real_model,
                    request_body=upstream_body,
                    status_code=response.status_code,
                    response_headers=response.headers,
                    debug_headers=debug_headers,
                    session_id=session_id,
                    request_path=request.url.path,
                    latency_ms=_elapsed_ms(attempt_start),
                ),
                attempt_order=attempt_order,
            )

        if is_stream:
            if candidate_provider == "codex":
                safe_create_task(
                    record_codex_usage_from_headers(
                        redis,
                        api_key_id=candidate.api_key.id,
                        headers=response.headers,
                    )
                )

            def _record_stream_attempt(
                outcome: str,
                failure_reason: str | None,
            ) -> None:
                _record_attempt_log(
                    request_id=request_id,
                    trace_id=trace_id,
                    model_alias=model_alias,
                    candidate=candidate,
                    requested_rule_group=requested_rule_group,
                    rule_group=effective_group,
                    attempt_order=attempt_order,
                    status_code=response.status_code,
                    outcome=outcome,
                    failure_reason=failure_reason,
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
                real_model=candidate.real_model,
                endpoint_id=candidate.endpoint.id,
                api_key_id=candidate.api_key.id,
                requested_rule_group=requested_rule_group,
                rule_group=effective_group,
                exposure_format=exposure_format,
                status_code=response.status_code,
                request_start=request_start,
                dump_rule=dump_rule,
                dump_endpoint_name=candidate.endpoint.name,
                dump_request_body=upstream_body,
                dump_session_id=session_id,
                dump_request_path=request.url.path,
                execution_mode=candidate.execution_mode,
                agent_node=agent_name,
                upstream_url=url,
                circuit_breaker=circuit_breaker,
                router_service=router_service,
                route_candidate=candidate,
                record_attempt=_record_stream_attempt,
            )
            return CandidateProxyResult(
                response=StreamingResponse(
                    generator,
                    status_code=response.status_code,
                    media_type=response.headers.get("content-type"),
                    headers=stream_headers,
                ),
                attempt_order=attempt_order,
            )

        latency_ms = int((time.perf_counter() - request_start) * 1000)
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
                outcome="fallback" if candidate != last_candidate else "returned",
                failure_reason=f"semantic_{semantic_failure_reason}",
                latency_ms=_elapsed_ms(attempt_start),
                agent_node=agent_name,
                upstream_url=url,
            )
            if candidate != last_candidate:
                break
            return CandidateProxyResult(
                response=raw_proxy_response_with_dump(
                    dump_rule,
                    content=content,
                    request_id=request_id,
                    trace_id=trace_id,
                    endpoint_name=candidate.endpoint.name,
                    endpoint_id=candidate.endpoint.id,
                    model_alias=model_alias,
                    real_model=candidate.real_model,
                    request_body=upstream_body,
                    status_code=response.status_code,
                    response_headers=response.headers,
                    debug_headers=debug_headers,
                    session_id=session_id,
                    request_path=request.url.path,
                    latency_ms=_elapsed_ms(attempt_start),
                ),
                attempt_order=attempt_order,
            )

        await circuit_breaker.record_success(candidate.api_key.id)
        await router_service.record_candidate_success(candidate)
        if candidate_provider == "codex":
            safe_create_task(
                record_codex_usage_from_headers(
                    redis,
                    api_key_id=candidate.api_key.id,
                    headers=response.headers,
                )
            )
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

        prompt_tokens, completion_tokens, total_tokens, cached_tokens = extract_usage(
            response_payload
        )
        metrics = RequestMetrics(
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            endpoint_id=candidate.endpoint.id,
            api_key_id=candidate.api_key.id,
            requested_rule_group=requested_rule_group,
            rule_group=effective_group,
            exposure_format=exposure_format,
            status_code=response.status_code,
            latency_ms=latency_ms,
            ttft_ms=None,
            tps=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            execution_mode=candidate.execution_mode,
            agent_node=agent_name,
            upstream_url=url,
        )
        safe_create_task(write_request_log(metrics))

        return CandidateProxyResult(
            response=raw_proxy_response_with_dump(
                dump_rule,
                content=content,
                request_id=request_id,
                trace_id=trace_id,
                endpoint_name=candidate.endpoint.name,
                endpoint_id=candidate.endpoint.id,
                model_alias=model_alias,
                real_model=candidate.real_model,
                request_body=upstream_body,
                status_code=response.status_code,
                response_headers=response.headers,
                debug_headers=debug_headers,
                session_id=session_id,
                request_path=request.url.path,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
            ),
            attempt_order=attempt_order,
        )

    return CandidateProxyResult(response=None, attempt_order=attempt_order)

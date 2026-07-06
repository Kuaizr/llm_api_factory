from dataclasses import dataclass
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.api.v1.route_modules.proxy_agent_streams import (
    agent_stream_generator as _agent_stream_generator,
)
from app.api.v1.route_modules.proxy_attempts import (
    elapsed_ms as _elapsed_ms,
    record_attempt_log as _record_attempt_log,
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
)
from app.services.agent_transport import AgentRequest, AgentUnavailableError, get_agent_manager
from app.services.background_tasks import safe_create_task
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.circuit_breaker import CircuitBreaker
from app.services.codex_usage import record_codex_usage_from_headers
from app.services.router import ModelRouter, RouteCandidate


@dataclass(frozen=True)
class CandidateProxyResult:
    response: Response | None
    attempt_order: int


async def handle_agent_candidate(
    *,
    request: Request,
    candidate: RouteCandidate,
    last_candidate: RouteCandidate,
    candidate_context: CandidateRequestContext,
    router_service: ModelRouter,
    circuit_breaker: CircuitBreaker,
    redis,
    client,
    request_id: str,
    trace_id: str,
    model_alias: str,
    requested_rule_group: str | None,
    effective_group: str,
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

    if agent_name is None:
        raise HTTPException(status_code=502, detail="Agent unavailable")

    agent_manager = get_agent_manager()
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
                else ("fallback" if candidate != last_candidate else "error"),
                failure_reason="agent_unavailable",
                latency_ms=_elapsed_ms(attempt_start),
                agent_node=agent_name,
                upstream_url=url,
            )
            await circuit_breaker.record_failure(candidate.api_key.id)
            if attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS:
                continue
            if candidate != last_candidate:
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
                agent_response = await agent_manager.send_request(agent_name, retry_request)
                status_code = agent_response.status_code or 500
            except AgentUnavailableError:
                await circuit_breaker.record_failure(candidate.api_key.id)
                if candidate != last_candidate:
                    break
                raise HTTPException(status_code=502, detail="Agent unavailable")
            except Exception as exc:
                await circuit_breaker.record_failure(candidate.api_key.id)
                if candidate != last_candidate:
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
                else ("fallback" if candidate != last_candidate else "returned"),
                failure_reason=f"http_{status_code}",
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
                    status_code=status_code,
                    response_headers=agent_response.headers,
                    debug_headers=debug_headers,
                    session_id=session_id,
                    request_path=request.url.path,
                    latency_ms=_elapsed_ms(attempt_start),
                ),
                attempt_order=attempt_order,
            )

        if is_stream:
            await circuit_breaker.record_success(candidate.api_key.id)
            await router_service.record_candidate_success(candidate)
            if candidate_provider == "codex":
                safe_create_task(
                    record_codex_usage_from_headers(
                        redis,
                        api_key_id=candidate.api_key.id,
                        headers=agent_response.headers,
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

            return CandidateProxyResult(
                response=StreamingResponse(
                    generator,
                    status_code=status_code,
                    media_type=agent_response.headers.get("content-type"),
                    headers=stream_headers,
                ),
                attempt_order=attempt_order,
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
                    content=agent_response.body,
                    request_id=request_id,
                    trace_id=trace_id,
                    endpoint_name=candidate.endpoint.name,
                    endpoint_id=candidate.endpoint.id,
                    model_alias=model_alias,
                    real_model=candidate.real_model,
                    request_body=upstream_body,
                    status_code=status_code,
                    response_headers=agent_response.headers,
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
                    headers=agent_response.headers,
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
            status_code=status_code,
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
            status_code=status_code,
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
                content=agent_response.body,
                request_id=request_id,
                trace_id=trace_id,
                endpoint_name=candidate.endpoint.name,
                endpoint_id=candidate.endpoint.id,
                model_alias=model_alias,
                real_model=candidate.real_model,
                request_body=upstream_body,
                status_code=status_code,
                response_headers=agent_response.headers,
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

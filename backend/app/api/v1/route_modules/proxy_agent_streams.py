import asyncio
import logging
import time
from typing import AsyncGenerator, Callable

from app.api.v1.route_helpers import _dump_proxy_record
from app.api.v1.route_proxy_helpers import _calculate_tps, _inspect_stream_chunk
from app.db.models import RoutingRule
from app.services.agent_transport import AgentStream
from app.services.background_tasks import safe_create_task
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.router import RouteCandidate


logger = logging.getLogger(__name__)


async def agent_stream_generator(
    *,
    agent_response: AgentStream,
    request_start: float,
    request_id: str,
    trace_id: str,
    model_alias: str,
    candidate: RouteCandidate,
    requested_rule_group: str | None,
    effective_group: str,
    exposure_format: str,
    status_code: int,
    agent_name: str | None,
    upstream_url: str,
    dump_rule: RoutingRule | None,
    upstream_body: bytes,
    session_id: str | None,
    request_path: str,
    circuit_breaker=None,
    router_service=None,
    record_attempt: Callable[[str, str | None], None] | None = None,
) -> AsyncGenerator[bytes, None]:
    buffer = ""
    usage_payload = None
    first_data_at: float | None = None
    chunks: list[bytes] = []
    stream_complete = False
    stream_failed = False
    response_completed = False
    failure_reason: str | None = None
    try:
        async for chunk in agent_response.iter_bytes():
            if chunk:
                if dump_rule is not None:
                    chunks.append(chunk)
                (
                    buffer,
                    usage_payload,
                    data_seen,
                    chunk_failed,
                    chunk_completed,
                ) = _inspect_stream_chunk(buffer, usage_payload, chunk)
                stream_failed = stream_failed or chunk_failed
                response_completed = response_completed or chunk_completed
                if data_seen and first_data_at is None:
                    first_data_at = time.perf_counter()
            yield chunk
        requires_completion = exposure_format in {"codex", "response"}
        stream_complete = not stream_failed and (
            response_completed or not requires_completion
        )
        if stream_failed:
            failure_reason = "upstream_stream_failed"
        elif not stream_complete:
            stream_failed = True
            failure_reason = "incomplete_stream"
            logger.warning(
                "Agent stream ended without response.completed request_id=%s "
                "api_key_id=%s agent=%s",
                request_id,
                candidate.api_key.id,
                agent_name,
            )
    except (asyncio.CancelledError, GeneratorExit):
        stream_complete = False
        failure_reason = "client_disconnect"
        raise
    except Exception as exc:
        stream_complete = False
        stream_failed = True
        failure_reason = "agent_stream_error"
        logger.warning(
            "Agent stream failed request_id=%s api_key_id=%s agent=%s error_type=%s",
            request_id,
            candidate.api_key.id,
            agent_name,
            type(exc).__name__,
        )
        raise
    finally:
        stream_end = time.perf_counter()
        latency_ms = int((stream_end - request_start) * 1000)
        if record_attempt is not None:
            if stream_complete:
                record_attempt("success", None)
            elif failure_reason == "client_disconnect":
                record_attempt("cancelled", failure_reason)
            else:
                record_attempt("error", failure_reason)
        if circuit_breaker is not None:
            if stream_failed:
                await circuit_breaker.record_failure(candidate.api_key.id)
            elif stream_complete:
                await circuit_breaker.record_success(candidate.api_key.id)
                if router_service is not None:
                    await router_service.record_candidate_success(candidate)
        ttft_ms = (
            int((first_data_at - request_start) * 1000)
            if first_data_at is not None
            else None
        )
        prompt_tokens, completion_tokens, total_tokens, cached_tokens = extract_usage(
            usage_payload
        )
        tps = _calculate_tps(first_data_at, stream_end, completion_tokens)
        metrics = RequestMetrics(
            request_id=request_id,
            trace_id=trace_id,
            model_alias=model_alias,
            endpoint_id=candidate.endpoint.id,
            api_key_id=candidate.api_key.id,
            requested_rule_group=requested_rule_group,
            rule_group=effective_group,
            exposure_format=exposure_format,
            status_code=status_code,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            tps=tps,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            execution_mode=candidate.execution_mode,
            agent_node=agent_name,
            upstream_url=upstream_url,
        )
        safe_create_task(write_request_log(metrics))
        if dump_rule is not None:
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
                    endpoint_id=candidate.endpoint.id,
                    real_model=candidate.real_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cached_tokens=cached_tokens,
                    latency_ms=latency_ms,
                    is_stream=True,
                    stream_complete=stream_complete,
                    session_id=session_id,
                    request_path=request_path,
                )
            )

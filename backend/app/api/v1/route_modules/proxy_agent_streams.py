import asyncio
import time
from typing import AsyncGenerator

from app.api.v1.route_helpers import _dump_proxy_record
from app.api.v1.route_proxy_helpers import _calculate_tps, _inspect_stream_chunk
from app.db.models import RoutingRule
from app.services.agent_transport import AgentResponse
from app.services.background_tasks import safe_create_task
from app.services.billing import RequestMetrics, extract_usage, write_request_log
from app.services.router import RouteCandidate


async def agent_stream_generator(
    *,
    agent_response: AgentResponse,
    request_start: float,
    request_id: str,
    trace_id: str,
    model_alias: str,
    candidate: RouteCandidate,
    requested_rule_group: str | None,
    effective_group: str,
    status_code: int,
    agent_name: str | None,
    upstream_url: str,
    dump_rule: RoutingRule | None,
    upstream_body: bytes,
    session_id: str | None,
    request_path: str,
) -> AsyncGenerator[bytes, None]:
    buffer = ""
    usage_payload = None
    first_data_at: float | None = None
    chunks: list[bytes] = []
    stream_complete = False
    try:
        async for chunk in agent_response.iter_bytes():
            if chunk:
                chunks.append(chunk)
                buffer, usage_payload, data_seen = _inspect_stream_chunk(
                    buffer, usage_payload, chunk
                )
                if data_seen and first_data_at is None:
                    first_data_at = time.perf_counter()
            yield chunk
        stream_complete = True
    except (asyncio.CancelledError, GeneratorExit):
        stream_complete = False
        raise
    except Exception:
        stream_complete = False
        raise
    finally:
        stream_end = time.perf_counter()
        ttft_ms = (
            int((first_data_at - request_start) * 1000)
            if first_data_at is not None
            else None
        )
        prompt_tokens, completion_tokens, total_tokens, cached_tokens = extract_usage(
            usage_payload
        )
        tps = _calculate_tps(first_data_at, stream_end, completion_tokens)
        latency_ms = (
            ttft_ms if ttft_ms is not None else int((stream_end - request_start) * 1000)
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

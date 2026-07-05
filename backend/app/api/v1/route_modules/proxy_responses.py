from fastapi.responses import Response

from app.api.v1.route_helpers import _dump_proxy_record
from app.api.v1.route_proxy_helpers import _filter_response_headers, _merge_headers
from app.services.background_tasks import safe_create_task


def raw_proxy_response_with_dump(
    dump_rule,
    *,
    content: bytes,
    request_id: str,
    trace_id: str,
    endpoint_name: str,
    endpoint_id: int,
    model_alias: str,
    real_model: str,
    request_body: bytes,
    status_code: int,
    response_headers: dict,
    debug_headers: dict,
    session_id: str | None,
    request_path: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cached_tokens: int | None = None,
    latency_ms: int | None = None,
) -> Response:
    safe_create_task(
        _dump_proxy_record(
            dump_rule,
            request_id,
            trace_id,
            endpoint_name,
            model_alias,
            request_body,
            content,
            status_code,
            endpoint_id=endpoint_id,
            real_model=real_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            latency_ms=latency_ms,
            is_stream=False,
            stream_complete=None,
            session_id=session_id,
            request_path=request_path,
        )
    )
    return Response(
        content=content,
        status_code=status_code,
        media_type=response_headers.get("content-type"),
        headers=_merge_headers(_filter_response_headers(response_headers), debug_headers),
    )

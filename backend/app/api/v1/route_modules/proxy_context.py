from dataclasses import dataclass
from typing import Callable

from fastapi import Request

from app.api.v1.route_modules.proxy_payloads import (
    is_stream_request,
    prepare_upstream_payload_and_body,
)
from app.api.v1.route_proxy_helpers import (
    _apply_oauth_access_token,
    _build_debug_headers,
    _build_target_url,
    _build_upstream_headers,
    _get_agent_name,
)
from app.core.providers import normalize_provider_name
from app.services.router import RouteCandidate


@dataclass(frozen=True)
class CandidateRequestContext:
    upstream_body: bytes
    headers: dict
    oauth_enabled: bool
    url: str
    is_stream: bool
    debug_headers: dict
    agent_name: str | None
    candidate_provider: str


async def prepare_candidate_request_context(
    request: Request,
    payload: dict[str, object],
    raw_body: bytes,
    candidate: RouteCandidate,
    *,
    rewrite_model: bool,
    trace_id: str,
    request_id: str,
    model_alias: str,
    include_internal_debug: bool,
    path_prefix: str | None,
    target_path_rewriter: Callable[[str, RouteCandidate], str] | None,
    model_payload_keys: tuple[str, ...],
    redis,
    client,
) -> CandidateRequestContext:
    candidate_provider = normalize_provider_name(
        getattr(candidate.endpoint, "provider", None)
    )
    request_is_stream = is_stream_request(request, payload)
    upstream_payload, upstream_body = prepare_upstream_payload_and_body(
        payload,
        raw_body,
        candidate,
        rewrite_model=rewrite_model,
        is_stream=request_is_stream,
        provider=candidate_provider,
        model_payload_keys=model_payload_keys,
    )
    headers = _build_upstream_headers(
        request.headers,
        candidate.endpoint,
        candidate.api_key.key,
        request_path=request.url.path,
        payload=payload,
    )
    headers, oauth_enabled = await _apply_oauth_access_token(
        headers,
        candidate.endpoint,
        redis,
        client,
    )
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
    return CandidateRequestContext(
        upstream_body=upstream_body,
        headers=headers,
        oauth_enabled=oauth_enabled,
        url=url,
        is_stream=is_stream_request(request, upstream_payload),
        debug_headers=_build_debug_headers(
            request_id,
            trace_id,
            candidate,
            model_alias,
            include_internal=include_internal_debug,
        ),
        agent_name=_get_agent_name(candidate.endpoint),
        candidate_provider=candidate_provider,
    )

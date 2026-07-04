import json

from fastapi import HTTPException, Request

from app.api.v1.route_proxy_helpers import _apply_request_body_template
from app.services.router import RouteCandidate


def parse_request_payload(raw_body: bytes) -> dict[str, object]:
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    return parsed


def resolve_model_alias(
    request: Request,
    payload: dict[str, object],
    *,
    rewrite_model: bool,
    allow_missing_model: bool,
    model_alias_override: str | None,
) -> str:
    model_alias_raw = payload.get("model")
    model_alias = model_alias_override
    if model_alias is None:
        model_alias = str(model_alias_raw) if model_alias_raw is not None else None
    header_model_alias = request.headers.get("X-Model-Alias")
    if not model_alias and header_model_alias:
        model_alias = str(header_model_alias)

    if rewrite_model and not model_alias and not allow_missing_model:
        raise HTTPException(status_code=400, detail="Missing model field")
    return model_alias or request.url.path


def extract_requested_rule_group(
    request: Request,
    payload: dict[str, object],
) -> str:
    payload_rule_group = payload.get("rule_group")
    if payload_rule_group is None:
        payload_rule_group = payload.get("rules")
    if not isinstance(payload_rule_group, str) or not payload_rule_group:
        payload_rule_group = request.headers.get("X-Rule-Group", "default")
    return str(payload_rule_group or "default").strip() or "default"


def prepare_upstream_payload_and_body(
    payload: dict[str, object],
    raw_body: bytes,
    candidate: RouteCandidate,
    *,
    rewrite_model: bool,
) -> tuple[dict[str, object], bytes]:
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
        return upstream_payload, json.dumps(upstream_payload).encode("utf-8")
    return upstream_payload, raw_body


def is_stream_request(request: Request, upstream_payload: dict[str, object]) -> bool:
    accept_header = request.headers.get("accept", "").lower()
    return bool(upstream_payload.get("stream")) or "text/event-stream" in accept_header

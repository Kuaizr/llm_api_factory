import uuid

from fastapi import Request

from app.api.v1.route_helpers import _extract_factory_api_key
from app.core.config import get_settings
from app.services.admin_auth import verify_admin_session_token

SESSION_HINT_KEYS = (
    "session_id",
    "conversation_id",
    "thread_id",
    "chat_id",
    "dialog_id",
    "previous_response_id",
)
TRACE_HINT_KEYS = ("trace_id", "request_id")


def include_debug_headers(request: Request) -> bool:
    debug_value = str(request.headers.get("X-Debug") or "").strip().lower()
    if debug_value not in {"1", "true", "yes"}:
        return False
    token = _extract_factory_api_key(request.headers)
    return verify_admin_session_token(token, get_settings())


def extract_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def clamp_identifier(value: str) -> str:
    return value[:128]


def extract_hint_from_payload(
    payload: dict[str, object],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = extract_text(payload.get(key))
        if value:
            return clamp_identifier(value)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in keys:
            value = extract_text(metadata.get(key))
            if value:
                return clamp_identifier(value)
    return None


def resolve_session_id(request: Request, payload: dict[str, object]) -> str | None:
    header_session = extract_text(request.headers.get("X-Session-Id"))
    if header_session:
        return clamp_identifier(header_session)

    payload_session = extract_hint_from_payload(payload, SESSION_HINT_KEYS)
    if payload_session:
        return payload_session

    user_value = extract_text(payload.get("user"))
    if user_value:
        return clamp_identifier(f"user:{user_value}")
    return None


def resolve_trace_id(
    request: Request,
    payload: dict[str, object],
    session_id: str | None,
) -> str:
    header_trace = extract_text(request.headers.get("X-Trace-Id"))
    if header_trace:
        return clamp_identifier(header_trace)

    payload_trace = extract_hint_from_payload(payload, TRACE_HINT_KEYS)
    if payload_trace:
        return payload_trace

    if session_id:
        return clamp_identifier(session_id)
    return uuid.uuid4().hex

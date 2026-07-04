import json

CANDIDATE_FALLBACK_STATUSES = {400, 401, 402, 403, 404, 429, 500, 502, 503, 504}
CIRCUIT_BREAKER_STATUSES = {401, 402, 403, 429, 500, 502, 503, 504}
LOCAL_RETRY_STATUSES = {429, 500, 502, 503, 504}
UPSTREAM_CANDIDATE_MAX_ATTEMPTS = 3
SEMANTIC_FAILURE_STATUSES = {"error", "failed", "failure", "cancelled", "canceled"}
SEMANTIC_FAILURE_MARKERS = (
    "error",
    "failed",
    "failure",
    "invalid",
    "unauthorized",
    "forbidden",
    "permission",
    "insufficient",
    "quota",
    "rate limit",
    "rate_limit",
    "too many requests",
    "unavailable",
    "overloaded",
    "busy",
    "timeout",
    "not supported",
    "unsupported",
    "model_not_supported",
    "service unavailable",
    "余额",
    "不足",
    "不可用",
    "限流",
    "风控",
    "失败",
    "错误",
    "封禁",
    "欠费",
)
SEMANTIC_SUCCESS_SIGNAL_KEYS = {
    "choices",
    "output",
    "output_text",
    "content",
    "candidates",
    "data",
    "embedding",
    "embeddings",
    "id",
}


def parse_json_object_bytes(content: bytes | None) -> dict[str, object] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def has_non_empty_value(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def contains_semantic_failure_marker(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict, list, tuple, set)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    normalized = text.strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in SEMANTIC_FAILURE_MARKERS)


def has_success_signal(payload: dict[str, object]) -> bool:
    if str(payload.get("object") or "").strip().lower() == "error":
        return False
    return any(
        key in payload and has_non_empty_value(payload.get(key))
        for key in SEMANTIC_SUCCESS_SIGNAL_KEYS
    )


def semantic_failure_reason(
    content: bytes,
    content_type: str | None,
    payload: dict[str, object] | None,
    provider: str,
) -> str | None:
    stripped = content.strip()
    if not stripped:
        return "empty_response_body"

    normalized_content_type = str(content_type or "").lower()
    if payload is None:
        text_sample = stripped[:4096].decode("utf-8", errors="ignore").lower()
        if "text/html" in normalized_content_type or text_sample.startswith(
            ("<html", "<!doctype html")
        ):
            return "html_response_body"
        if contains_semantic_failure_marker(text_sample):
            return "text_error_body"
        return None

    if has_non_empty_value(payload.get("error")):
        return "error_field"
    if has_non_empty_value(payload.get("errors")):
        return "errors_field"

    object_type = str(payload.get("object") or "").strip().lower()
    if object_type == "error":
        return "error_object"

    response_type = str(payload.get("type") or "").strip().lower()
    if response_type == "error":
        return "error_type"

    status = str(payload.get("status") or "").strip().lower()
    if status in SEMANTIC_FAILURE_STATUSES:
        return "failure_status"

    for flag_key in ("success", "ok"):
        if payload.get(flag_key) is False:
            return f"{flag_key}_false"

    for code_key in ("status_code", "code"):
        code_value = payload.get(code_key)
        if isinstance(code_value, int) and code_value >= 400:
            return f"{code_key}_failure"
        if isinstance(code_value, str) and contains_semantic_failure_marker(code_value):
            return f"{code_key}_failure"

    if provider == "gemini" and not has_non_empty_value(payload.get("candidates")):
        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and has_non_empty_value(
            prompt_feedback.get("blockReason")
        ):
            return "gemini_blocked"

    if not has_success_signal(payload):
        for message_key in ("message", "msg", "detail"):
            if contains_semantic_failure_marker(payload.get(message_key)):
                return f"{message_key}_failure"

    return None


def should_retry_same_candidate(status_code: int, attempt_index: int) -> bool:
    return (
        status_code in LOCAL_RETRY_STATUSES
        and attempt_index + 1 < UPSTREAM_CANDIDATE_MAX_ATTEMPTS
    )

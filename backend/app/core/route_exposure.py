from __future__ import annotations

DEFAULT_EXPOSURE_FORMAT = "any"
EXPOSURE_FORMAT_ANY = "any"
EXPOSURE_FORMAT_CHAT = "chat"
EXPOSURE_FORMAT_CODEX = "codex"
EXPOSURE_FORMAT_RESPONSE = "response"
EXPOSURE_FORMAT_CLAUDE_CODE = "claude_code"
EXPOSURE_FORMAT_MESSAGE = "message"
EXPOSURE_FORMAT_GEMINI = "gemini"

SUPPORTED_EXPOSURE_FORMATS = {
    EXPOSURE_FORMAT_ANY,
    EXPOSURE_FORMAT_CHAT,
    EXPOSURE_FORMAT_CODEX,
    EXPOSURE_FORMAT_RESPONSE,
    EXPOSURE_FORMAT_CLAUDE_CODE,
    EXPOSURE_FORMAT_MESSAGE,
    EXPOSURE_FORMAT_GEMINI,
}

EXPLICIT_EXPOSURE_FORMATS = (
    EXPOSURE_FORMAT_CHAT,
    EXPOSURE_FORMAT_RESPONSE,
    EXPOSURE_FORMAT_CODEX,
    EXPOSURE_FORMAT_MESSAGE,
    EXPOSURE_FORMAT_CLAUDE_CODE,
    EXPOSURE_FORMAT_GEMINI,
)


def normalize_exposure_format(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_EXPOSURE_FORMAT
    normalized = value.strip().lower().replace("-", "_")
    if normalized == "responses":
        normalized = EXPOSURE_FORMAT_RESPONSE
    if normalized in {"claudecode", "claude-code"}:
        normalized = EXPOSURE_FORMAT_CLAUDE_CODE
    if normalized not in SUPPORTED_EXPOSURE_FORMATS:
        return DEFAULT_EXPOSURE_FORMAT
    return normalized


def normalize_exposure_formats(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    normalized: list[str] = []
    for item in value:
        exposure_format = normalize_exposure_format(item)
        if exposure_format not in normalized:
            normalized.append(exposure_format)
    return normalized


def exposure_format_matches(configured: object, requested: object) -> bool:
    return exposure_format_match_priority(configured, requested) is not None


def exposure_format_match_priority(
    configured: object, requested: object
) -> int | None:
    normalized_configured = normalize_exposure_formats(configured)
    normalized_requested = normalize_exposure_format(requested)
    if normalized_requested == EXPOSURE_FORMAT_ANY:
        return 1
    if normalized_requested in normalized_configured:
        return 2
    return None

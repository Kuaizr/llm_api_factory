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


def exposure_format_matches(configured: object, requested: object) -> bool:
    normalized_configured = normalize_exposure_format(configured)
    normalized_requested = normalize_exposure_format(requested)
    return (
        normalized_configured == EXPOSURE_FORMAT_ANY
        or normalized_requested == EXPOSURE_FORMAT_ANY
        or normalized_configured == normalized_requested
    )

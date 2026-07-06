from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Mapping

CODEX_USAGE_REDIS_PREFIX = "codex:usage"
CODEX_USAGE_TTL_SECONDS = 8 * 24 * 3600


@dataclass(frozen=True)
class CodexUsageWindow:
    used_percent: float | None = None
    reset_after_seconds: int | None = None
    window_minutes: int | None = None


@dataclass(frozen=True)
class CodexUsageSnapshot:
    primary: CodexUsageWindow
    secondary: CodexUsageWindow
    updated_at: int


def _key(api_key_id: int) -> str:
    return f"{CODEX_USAGE_REDIS_PREFIX}:{api_key_id}"


def _lookup_header(headers: Mapping[str, object], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_window(headers: Mapping[str, object], prefix: str) -> CodexUsageWindow:
    return CodexUsageWindow(
        used_percent=_parse_float(
            _lookup_header(headers, f"x-codex-{prefix}-used-percent")
        ),
        reset_after_seconds=_parse_int(
            _lookup_header(headers, f"x-codex-{prefix}-reset-after-seconds")
        ),
        window_minutes=_parse_int(
            _lookup_header(headers, f"x-codex-{prefix}-window-minutes")
        ),
    )


def parse_codex_usage_headers(
    headers: Mapping[str, object],
) -> CodexUsageSnapshot | None:
    primary = _parse_window(headers, "primary")
    secondary = _parse_window(headers, "secondary")
    if (
        primary.used_percent is None
        and primary.reset_after_seconds is None
        and primary.window_minutes is None
        and secondary.used_percent is None
        and secondary.reset_after_seconds is None
        and secondary.window_minutes is None
    ):
        return None
    return CodexUsageSnapshot(
        primary=primary,
        secondary=secondary,
        updated_at=int(time.time()),
    )


async def record_codex_usage_from_headers(
    redis,
    *,
    api_key_id: int,
    headers: Mapping[str, object],
) -> None:
    snapshot = parse_codex_usage_headers(headers)
    if snapshot is None:
        return
    await redis.set(
        _key(api_key_id),
        json.dumps(asdict(snapshot), separators=(",", ":")),
        ex=CODEX_USAGE_TTL_SECONDS,
    )


async def read_codex_usage_many(redis, api_key_ids: list[int]) -> dict[int, dict[str, object]]:
    if not api_key_ids:
        return {}
    values = await redis.mget([_key(api_key_id) for api_key_id in api_key_ids])
    result: dict[int, dict[str, object]] = {}
    for api_key_id, raw in zip(api_key_ids, values, strict=False):
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            result[api_key_id] = parsed
    return result

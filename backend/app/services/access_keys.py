from __future__ import annotations

import hashlib

ACCESS_KEY_HASH_PREFIX = "sha256:"


def hash_access_key(value: str) -> str:
    normalized = str(value or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{ACCESS_KEY_HASH_PREFIX}{digest}"


def is_hashed_access_key(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ACCESS_KEY_HASH_PREFIX)


def access_key_preview(value: str | None) -> str:
    normalized = str(value or "").strip()
    if len(normalized) <= 6:
        return normalized
    return f"{normalized[:3]}...{normalized[-4:]}"

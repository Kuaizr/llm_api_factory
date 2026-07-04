from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import Settings, get_settings

ADMIN_SESSION_PREFIX = "adm"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(f"{raw}{padding}".encode("ascii"))


def _session_secret(settings: Settings) -> bytes | None:
    secret = settings.master_auth_token
    if not secret:
        return None
    return secret.encode("utf-8")


def _sign(payload_b64: str, settings: Settings) -> str | None:
    secret = _session_secret(settings)
    if secret is None:
        return None
    digest = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def issue_admin_session_token(settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    now = int(time.time())
    payload: dict[str, Any] = {
        "iat": now,
        "nonce": secrets.token_urlsafe(18),
    }
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _sign(payload_b64, resolved)
    if signature is None:
        raise ValueError("Master auth is disabled")
    return f"{ADMIN_SESSION_PREFIX}.{payload_b64}.{signature}"


def verify_admin_session_token(
    token: str | None,
    settings: Settings | None = None,
) -> bool:
    if not token:
        return False
    resolved = settings or get_settings()
    if getattr(resolved, "admin_legacy_master_bearer_enabled", False):
        expected = resolved.master_auth_token
        if expected and hmac.compare_digest(token, expected):
            return True
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != ADMIN_SESSION_PREFIX:
        return False
    _, payload_b64, supplied_signature = parts
    expected_signature = _sign(payload_b64, resolved)
    if expected_signature is None:
        return False
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return False
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        return False
    ttl_seconds = max(1, int(resolved.admin_session_ttl_seconds))
    return int(time.time()) - issued_at <= ttl_seconds


def verify_master_password(candidate: str | None, settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    expected = resolved.master_auth_token
    if not expected or candidate is None:
        return False
    return hmac.compare_digest(candidate, expected)

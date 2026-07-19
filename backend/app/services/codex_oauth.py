from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import APIKey
from app.services.secrets import decrypt_secret_value, encrypt_secret_value_if_possible

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexCredential:
    access_token: str
    account_id: str
    refresh_token: str | None = None
    expires_at: int | None = None
    raw: dict[str, Any] | None = None


class CodexCredentialError(RuntimeError):
    pass


def build_codex_models_url(base_url: str, *, client_version: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    encoded_version = quote(client_version.strip() or "0.144.3", safe="")
    return f"{cleaned}/backend-api/codex/models?client_version={encoded_version}"


def build_codex_headers(
    credential: CodexCredential,
    *,
    client_version: str,
    accept: str = "application/json",
) -> dict[str, str]:
    version = client_version.strip() or "0.144.3"
    return {
        "Authorization": f"Bearer {credential.access_token}",
        "chatgpt-account-id": credential.account_id,
        "Content-Type": "application/json",
        "Accept": accept,
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
        "User-Agent": f"codex-cli/{version}",
    }


def _parse_timestamp(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        if trimmed.isdigit():
            return int(trimmed)
        try:
            parsed_dt = datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        return int(parsed_dt.timestamp())
    return None


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_account_id_from_claims(claims: dict[str, Any]) -> str | None:
    direct_keys = (
        "chatgpt_account_id",
        "account_id",
        "https://api.openai.com/chatgpt_account_id",
    )
    for key in direct_keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        for key in ("chatgpt_account_id", "account_id"):
            value = auth_claim.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def parse_codex_credential(raw: str) -> CodexCredential:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CodexCredentialError("Codex credential must be a JSON object") from exc
    if not isinstance(data, dict):
        raise CodexCredentialError("Codex credential must be a JSON object")

    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip() or None
    account_id = str(
        data.get("account_id")
        or data.get("chatgpt_account_id")
        or ""
    ).strip()
    if not account_id and access_token:
        account_id = _extract_account_id_from_claims(_decode_jwt_payload(access_token)) or ""

    expires_at = (
        _parse_timestamp(data.get("expires_at"))
        or _parse_timestamp(data.get("expiry"))
        or _parse_timestamp(data.get("expired"))
        or _parse_timestamp(data.get("expires"))
    )
    if not access_token and not refresh_token:
        raise CodexCredentialError("Codex credential requires access_token or refresh_token")
    if access_token and not account_id and not refresh_token:
        raise CodexCredentialError("Codex credential requires account_id")
    return CodexCredential(
        access_token=access_token,
        account_id=account_id,
        refresh_token=refresh_token,
        expires_at=expires_at,
        raw=data,
    )


def _needs_refresh(
    credential: CodexCredential,
    *,
    settings: Settings,
    now: int | None = None,
) -> bool:
    if not credential.access_token:
        return True
    if not credential.account_id and credential.refresh_token:
        return True
    if credential.expires_at is None:
        return False
    current = int(now if now is not None else time.time())
    return credential.expires_at <= current + settings.codex_oauth_refresh_leeway_seconds


async def _refresh_codex_credential(
    credential: CodexCredential,
    *,
    client,
    settings: Settings,
) -> CodexCredential:
    if not credential.refresh_token:
        raise CodexCredentialError("Codex credential is expired and has no refresh_token")
    response = await client.post(
        settings.codex_oauth_token_url,
        data={
            "client_id": settings.codex_oauth_client_id,
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    try:
        content = await response.aread()
    finally:
        await response.aclose()
    if response.status_code >= 400:
        raise CodexCredentialError(f"Codex token refresh failed: HTTP {response.status_code}")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CodexCredentialError("Codex token refresh returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CodexCredentialError("Codex token refresh returned invalid JSON")

    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise CodexCredentialError("Codex token refresh returned no access_token")
    refresh_token = str(payload.get("refresh_token") or "").strip() or credential.refresh_token
    expires_in = _parse_timestamp(payload.get("expires_in"))
    expires_at = int(time.time()) + expires_in if expires_in else None
    account_id = str(
        payload.get("account_id")
        or payload.get("chatgpt_account_id")
        or credential.account_id
        or ""
    ).strip()
    if not account_id:
        account_id = _extract_account_id_from_claims(_decode_jwt_payload(access_token)) or ""
    if not account_id:
        raise CodexCredentialError("Codex token refresh returned no account_id")

    raw = dict(credential.raw or {})
    raw.update(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
            "expires_at": expires_at,
            "last_refresh": int(time.time()),
        }
    )
    return CodexCredential(
        access_token=access_token,
        account_id=account_id,
        refresh_token=refresh_token,
        expires_at=expires_at,
        raw=raw,
    )


async def resolve_codex_credential(
    api_key: APIKey,
    *,
    client,
    session: AsyncSession | None = None,
    settings: Settings | None = None,
) -> CodexCredential:
    resolved_settings = settings or get_settings()
    decrypted = decrypt_secret_value(api_key.key, settings=resolved_settings)
    credential = parse_codex_credential(decrypted)
    if not _needs_refresh(credential, settings=resolved_settings):
        return credential

    refreshed = await _refresh_codex_credential(
        credential,
        client=client,
        settings=resolved_settings,
    )
    if session is not None and refreshed.raw is not None:
        api_key.key = encrypt_secret_value_if_possible(
            json.dumps(refreshed.raw, ensure_ascii=False, separators=(",", ":")),
            settings=resolved_settings,
        ) or api_key.key
        try:
            await session.commit()
        except Exception:
            logger.exception("Failed to persist refreshed Codex credential")
            try:
                await session.rollback()
            except Exception:
                logger.exception("Failed to rollback Codex credential refresh")
    return refreshed

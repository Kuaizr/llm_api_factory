from __future__ import annotations

import base64
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import APIKey
from app.services.endpoint_transport import send_endpoint_request
from app.services.secrets import decrypt_secret_value, encrypt_secret_value_if_possible

logger = logging.getLogger(__name__)

CODEX_REFRESH_LOCK_TTL_SECONDS = 30
CODEX_REFRESH_WAIT_STEP_SECONDS = 0.1
CODEX_REFRESH_WAIT_ROUNDS = 300
CODEX_REFRESH_CACHE_TTL_SECONDS = 60
_local_refresh_locks: dict[int, asyncio.Lock] = {}


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


def apply_codex_auth_headers(
    headers: dict[str, str], credential: CodexCredential
) -> dict[str, str]:
    updated = {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"authorization", "chatgpt-account-id"}
    }
    updated["Authorization"] = f"Bearer {credential.access_token}"
    updated["chatgpt-account-id"] = credential.account_id
    return updated


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

    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    access_token = str(data.get("access_token") or tokens.get("access_token") or "").strip()
    refresh_token = (
        str(data.get("refresh_token") or tokens.get("refresh_token") or "").strip()
        or None
    )
    account_id = str(
        data.get("account_id")
        or data.get("chatgpt_account_id")
        or tokens.get("account_id")
        or tokens.get("chatgpt_account_id")
        or ""
    ).strip()
    if not account_id and access_token:
        account_id = _extract_account_id_from_claims(_decode_jwt_payload(access_token)) or ""

    access_token_claims = _decode_jwt_payload(access_token) if access_token else {}
    expires_at = (
        _parse_timestamp(data.get("expires_at"))
        or _parse_timestamp(data.get("expiry"))
        or _parse_timestamp(data.get("expired"))
        or _parse_timestamp(data.get("expires"))
        or _parse_timestamp(tokens.get("expires_at"))
        or _parse_timestamp(access_token_claims.get("exp"))
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


def normalize_codex_credential_json(raw: str) -> str:
    credential = parse_codex_credential(raw)
    normalized: dict[str, object] = {}
    if credential.access_token:
        normalized["access_token"] = credential.access_token
    if credential.refresh_token:
        normalized["refresh_token"] = credential.refresh_token
    if credential.account_id:
        normalized["account_id"] = credential.account_id
    if credential.expires_at is not None:
        normalized["expires_at"] = credential.expires_at
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


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
    endpoint: object | None = None,
) -> CodexCredential:
    if not credential.refresh_token:
        raise CodexCredentialError("Codex credential is expired and has no refresh_token")
    body = urlencode(
        {
            "client_id": settings.codex_oauth_client_id,
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
        }
    ).encode("utf-8")
    response = await send_endpoint_request(
        endpoint=endpoint,
        method="POST",
        url=settings.codex_oauth_token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=body,
        client=client,
        timeout=30.0,
    )
    content = response.body
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


def _refresh_lock_key(api_key_id: int) -> str:
    return f"codex:oauth:refresh-lock:{api_key_id}"


def _refresh_cache_key(api_key_id: int) -> str:
    return f"codex:oauth:refresh-result:{api_key_id}"


def _encode_refresh_result(credential: CodexCredential) -> str:
    return json.dumps(
        {
            "access_token": credential.access_token,
            "account_id": credential.account_id,
            "expires_at": credential.expires_at,
            "refreshed_at": time.time(),
        },
        separators=(",", ":"),
    )


def _decode_refresh_result(
    raw: object, *, min_refreshed_at: float | None = None
) -> CodexCredential | None:
    if not isinstance(raw, (str, bytes)):
        return None
    try:
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    refreshed_at = payload.get("refreshed_at")
    if min_refreshed_at is not None and (
        not isinstance(refreshed_at, (int, float))
        or float(refreshed_at) < min_refreshed_at
    ):
        return None
    access_token = str(payload.get("access_token") or "").strip()
    account_id = str(payload.get("account_id") or "").strip()
    if not access_token or not account_id:
        return None
    return CodexCredential(
        access_token=access_token,
        account_id=account_id,
        expires_at=_parse_timestamp(payload.get("expires_at")),
    )


async def _wait_for_refresh_result(
    redis: object,
    api_key_id: int,
    *,
    min_refreshed_at: float | None = None,
) -> CodexCredential | None:
    cache_key = _refresh_cache_key(api_key_id)
    for _ in range(CODEX_REFRESH_WAIT_ROUNDS):
        cached = _decode_refresh_result(
            await redis.get(cache_key), min_refreshed_at=min_refreshed_at
        )
        if cached is not None:
            return cached
        await asyncio.sleep(CODEX_REFRESH_WAIT_STEP_SECONDS)
    return None


async def _persist_refreshed_credential(
    api_key: APIKey,
    refreshed: CodexCredential,
    *,
    session: AsyncSession | None,
    settings: Settings,
) -> None:
    if refreshed.raw is None:
        return
    encrypted = encrypt_secret_value_if_possible(
        json.dumps(refreshed.raw, ensure_ascii=False, separators=(",", ":")),
        settings=settings,
    )
    if not encrypted:
        return
    api_key.key = encrypted
    if session is not None:
        flush = getattr(session, "flush", None)
        if flush is not None:
            await flush()


async def resolve_codex_credential(
    api_key: APIKey,
    *,
    client,
    session: AsyncSession | None = None,
    session_factory=None,
    redis: object | None = None,
    settings: Settings | None = None,
    force_refresh: bool = False,
    endpoint: object | None = None,
) -> CodexCredential:
    resolved_settings = settings or get_settings()
    decrypted = decrypt_secret_value(api_key.key, settings=resolved_settings)
    credential = parse_codex_credential(decrypted)
    if not force_refresh and not _needs_refresh(credential, settings=resolved_settings):
        return credential

    refresh_requested_at = time.time()
    api_key_id = int(getattr(api_key, "id", 0) or 0)
    local_lock = _local_refresh_locks.setdefault(api_key_id, asyncio.Lock())
    async with local_lock:
        owned_session = None
        stored_api_key = api_key
        persistence_session = session
        if session_factory is not None and api_key_id > 0:
            owned_session = session_factory()
            persistence_session = await owned_session.__aenter__()
            loaded_api_key = await persistence_session.get(APIKey, api_key_id)
            if loaded_api_key is not None:
                stored_api_key = loaded_api_key
        elif persistence_session is not None:
            try:
                await persistence_session.refresh(stored_api_key)
            except Exception:
                logger.debug("Unable to refresh Codex API key row before token refresh")

        try:
            credential = parse_codex_credential(
                decrypt_secret_value(stored_api_key.key, settings=resolved_settings)
            )
            if not force_refresh and not _needs_refresh(
                credential, settings=resolved_settings
            ):
                return credential

            if redis is not None and force_refresh and api_key_id > 0:
                cached = _decode_refresh_result(
                    await redis.get(_refresh_cache_key(api_key_id)),
                    min_refreshed_at=refresh_requested_at,
                )
                if cached is not None:
                    return cached

            has_redis_lock = False
            if redis is not None and api_key_id > 0:
                has_redis_lock = bool(
                    await redis.set(
                        _refresh_lock_key(api_key_id),
                        "1",
                        ex=CODEX_REFRESH_LOCK_TTL_SECONDS,
                        nx=True,
                    )
                )
                if not has_redis_lock:
                    cached = await _wait_for_refresh_result(
                        redis,
                        api_key_id,
                        min_refreshed_at=refresh_requested_at,
                    )
                    if cached is not None:
                        return cached
                    raise CodexCredentialError("Codex token refresh lock timeout")

            try:
                refreshed = await _refresh_codex_credential(
                    credential,
                    client=client,
                    settings=resolved_settings,
                    endpoint=endpoint,
                )
                await _persist_refreshed_credential(
                    stored_api_key,
                    refreshed,
                    session=persistence_session,
                    settings=resolved_settings,
                )
                if persistence_session is not None:
                    await persistence_session.commit()
                elif stored_api_key is api_key and refreshed.raw is not None:
                    api_key.key = encrypt_secret_value_if_possible(
                        json.dumps(
                            refreshed.raw,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        settings=resolved_settings,
                    ) or api_key.key
                if redis is not None and api_key_id > 0:
                    await redis.set(
                        _refresh_cache_key(api_key_id),
                        _encode_refresh_result(refreshed),
                        ex=CODEX_REFRESH_CACHE_TTL_SECONDS,
                    )
                return refreshed
            finally:
                if redis is not None and has_redis_lock:
                    await redis.delete(_refresh_lock_key(api_key_id))
        finally:
            if owned_session is not None:
                await owned_session.__aexit__(None, None, None)

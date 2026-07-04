from __future__ import annotations

import base64
import hashlib
import json
import logging
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings, get_settings

ENCRYPTED_SECRET_PREFIX = "enc:v1:"
MASKED_SECRET_VALUE = "********"
SECRET_FIELD_MARKERS = ("secret", "password", "private_key", "refresh_token")

logger = logging.getLogger(__name__)


class SecretEncryptionUnavailable(RuntimeError):
    pass


class SecretDecryptionError(RuntimeError):
    pass


def _source_secret(settings: Settings) -> str | None:
    configured = (settings.data_encryption_key or "").strip()
    if configured:
        return configured
    fallback = (settings.master_auth_token or "").strip()
    if fallback:
        return fallback
    return None


@lru_cache(maxsize=32)
def _build_fernet(source_secret: str) -> Fernet:
    raw = source_secret.encode("utf-8")
    try:
        return Fernet(raw)
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encryption_available(settings: Settings | None = None) -> bool:
    return _source_secret(settings or get_settings()) is not None


def _fernet(settings: Settings | None = None) -> Fernet:
    resolved = settings or get_settings()
    source_secret = _source_secret(resolved)
    if not source_secret:
        raise SecretEncryptionUnavailable(
            "LLM_DATA_ENCRYPTION_KEY or LLM_MASTER_AUTH_TOKEN is required to encrypt secrets"
        )
    return _build_fernet(source_secret)


def is_encrypted_secret(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_SECRET_PREFIX)


def encrypt_secret_value(value: str | None, settings: Settings | None = None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text or is_encrypted_secret(text):
        return text
    token = _fernet(settings).encrypt(text.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_SECRET_PREFIX}{token}"


def encrypt_secret_value_if_possible(
    value: str | None,
    settings: Settings | None = None,
) -> str | None:
    if value is None or is_encrypted_secret(value):
        return value
    if not encryption_available(settings):
        return value
    return encrypt_secret_value(value, settings=settings)


def decrypt_secret_value(value: str | None, settings: Settings | None = None) -> str:
    if value is None:
        return ""
    text = str(value)
    if not is_encrypted_secret(text):
        return text
    token = text[len(ENCRYPTED_SECRET_PREFIX) :]
    try:
        return _fernet(settings).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, SecretEncryptionUnavailable) as exc:
        raise SecretDecryptionError("Unable to decrypt stored secret") from exc


def _is_secret_field(name: object) -> bool:
    lowered = str(name or "").lower()
    return any(marker in lowered for marker in SECRET_FIELD_MARKERS)


def encrypt_oauth_config(
    config: dict[str, Any] | None,
    settings: Settings | None = None,
) -> dict[str, str] | None:
    if config is None:
        return None
    encrypted: dict[str, str] = {}
    for key, value in config.items():
        key_text = str(key)
        value_text = str(value)
        if _is_secret_field(key_text) and value_text:
            encrypted[key_text] = encrypt_secret_value(value_text, settings=settings) or ""
        else:
            encrypted[key_text] = value_text
    return encrypted


def encrypt_oauth_config_if_possible(
    config: dict[str, Any] | None,
    settings: Settings | None = None,
) -> dict[str, str] | None:
    if config is None:
        return None
    encrypted: dict[str, str] = {}
    for key, value in config.items():
        key_text = str(key)
        value_text = str(value)
        if _is_secret_field(key_text) and value_text:
            encrypted[key_text] = (
                encrypt_secret_value_if_possible(value_text, settings=settings) or ""
            )
        else:
            encrypted[key_text] = value_text
    return encrypted


def decrypt_oauth_config(
    config: dict[str, Any] | None,
    settings: Settings | None = None,
) -> dict[str, str] | None:
    if config is None:
        return None
    decrypted: dict[str, str] = {}
    for key, value in config.items():
        key_text = str(key)
        value_text = str(value)
        if _is_secret_field(key_text) and value_text:
            decrypted[key_text] = decrypt_secret_value(value_text, settings=settings)
        else:
            decrypted[key_text] = value_text
    return decrypted


def mask_secret_value(value: str | None, settings: Settings | None = None) -> str:
    decrypted = decrypt_secret_value(value, settings=settings)
    if len(decrypted) <= 6:
        return decrypted
    return f"{decrypted[:3]}...{decrypted[-4:]}"


def mask_oauth_config(config: object) -> dict[str, str] | None:
    if not isinstance(config, dict):
        return None
    masked: dict[str, str] = {}
    for key, value in config.items():
        key_text = str(key)
        value_text = str(value)
        if _is_secret_field(key_text) and value_text:
            masked[key_text] = MASKED_SECRET_VALUE
        else:
            masked[key_text] = value_text
    return masked


def merge_masked_oauth_config(
    existing_raw: str | None,
    incoming: dict[str, Any],
) -> dict[str, str]:
    existing: dict[str, str] = {}
    if existing_raw:
        try:
            parsed = json.loads(existing_raw)
            if isinstance(parsed, dict):
                existing = {str(key): str(value) for key, value in parsed.items()}
        except (json.JSONDecodeError, TypeError):
            existing = {}
    merged = {str(key): str(value) for key, value in incoming.items()}
    for key, value in list(merged.items()):
        if _is_secret_field(key) and value == MASKED_SECRET_VALUE and key in existing:
            merged[key] = existing[key]
    return merged

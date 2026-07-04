from __future__ import annotations

from datetime import date, datetime
import json
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

MASKED_AUDIT_VALUE = "********"
SENSITIVE_EXACT_FIELDS = {
    "api_key",
    "authorization",
    "cookie",
    "extra_cookies",
    "extra_headers",
    "key",
    "oauth_config",
    "set-cookie",
    "x-api-key",
}
SENSITIVE_FIELD_MARKERS = (
    "oauth",
    "password",
    "secret",
    "token",
)


def _is_sensitive_field(name: object) -> bool:
    lowered = str(name or "").lower()
    if lowered in SENSITIVE_EXACT_FIELDS:
        return True
    return any(marker in lowered for marker in SENSITIVE_FIELD_MARKERS)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def sanitize_audit_value(value: Any, *, field_name: str | None = None) -> Any:
    if field_name and _is_sensitive_field(field_name):
        return MASKED_AUDIT_VALUE if value not in (None, "") else value
    if isinstance(value, dict):
        return {
            str(key): sanitize_audit_value(nested, field_name=str(key))
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_audit_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def audit_snapshot(value: Any) -> dict[str, Any] | list[Any] | Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return sanitize_audit_value(value)
    if isinstance(value, (list, tuple, set)):
        return [audit_snapshot(item) for item in value]

    try:
        inspection = sa_inspect(value)
    except Exception:
        return sanitize_audit_value(value)

    snapshot: dict[str, Any] = {}
    for attr in inspection.mapper.column_attrs:
        field = attr.key
        snapshot[field] = sanitize_audit_value(getattr(value, field), field_name=field)
    return snapshot


def _serialize_snapshot(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _resource_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def record_audit_log(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: object | None = None,
    resource_name: str | None = None,
    before: Any = None,
    after: Any = None,
    actor: str = "admin",
) -> AuditLog:
    log = AuditLog(
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=_resource_id(resource_id),
        resource_name=resource_name,
        before_json=_serialize_snapshot(audit_snapshot(before)),
        after_json=_serialize_snapshot(audit_snapshot(after)),
    )
    session.add(log)
    return log

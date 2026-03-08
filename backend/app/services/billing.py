from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.db.models import APIKey, RequestLog
from app.db.session import SessionLocal


@dataclass(frozen=True)
class RequestMetrics:
    request_id: str
    trace_id: str
    model_alias: str
    endpoint_id: int
    api_key_id: int
    rule_group: str
    status_code: int
    latency_ms: int
    ttft_ms: int | None
    tps: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def extract_usage(payload: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    if not payload:
        return None, None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    return (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )


async def write_request_log(metrics: RequestMetrics) -> None:
    async with SessionLocal() as session:
        log = RequestLog(
            request_id=metrics.request_id,
            trace_id=metrics.trace_id,
            model_alias=metrics.model_alias,
            endpoint_id=metrics.endpoint_id,
            api_key_id=metrics.api_key_id,
            rule_group=metrics.rule_group,
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
            total_tokens=metrics.total_tokens,
            latency_ms=metrics.latency_ms,
            ttft_ms=metrics.ttft_ms,
            tps=metrics.tps,
            status_code=metrics.status_code,
        )
        session.add(log)

        tokens = metrics.total_tokens
        if tokens is None:
            tokens = (metrics.prompt_tokens or 0) + (metrics.completion_tokens or 0)
        api_key = await session.get(APIKey, metrics.api_key_id)
        if api_key is not None:
            today = datetime.now(timezone.utc).date()
            if api_key.used_today_date != today:
                api_key.used_today = 0
                api_key.used_today_date = today
            api_key.used_today = (api_key.used_today or 0) + tokens
            api_key.total_usage = (api_key.total_usage or 0) + tokens

        await session.commit()

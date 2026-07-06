from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, update

from app.core.timezone import app_today
from app.db.models import APIKey, RequestAttemptLog, RequestLog
from app.db.session import SessionLocal


def _usage_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
    return None


@dataclass(frozen=True)
class RequestMetrics:
    request_id: str
    trace_id: str
    model_alias: str
    endpoint_id: int
    api_key_id: int
    requested_rule_group: str | None
    rule_group: str
    status_code: int
    latency_ms: int
    ttft_ms: int | None
    tps: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_tokens: int | None = None
    execution_mode: str = "direct"
    agent_node: str | None = None
    upstream_url: str | None = None


@dataclass(frozen=True)
class RequestAttemptMetrics:
    request_id: str
    trace_id: str
    model_alias: str
    endpoint_id: int
    api_key_id: int
    requested_rule_group: str | None
    rule_group: str
    attempt_order: int
    status_code: int | None
    outcome: str
    failure_reason: str | None
    latency_ms: int
    execution_mode: str = "direct"
    agent_node: str | None = None
    upstream_url: str | None = None


def extract_usage(
    payload: dict[str, Any] | None,
) -> tuple[int | None, int | None, int | None, int | None]:
    if not payload:
        return None, None, None, None

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            usage = metadata.get("total_usage") or metadata.get("usage")
    if not isinstance(usage, dict):
        usage = payload.get("total_usage")
    if not isinstance(usage, dict):
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, dict) and isinstance(choice.get("usage"), dict):
                    usage = choice["usage"]
                    break
    if not isinstance(usage, dict):
        cached_tokens = _extract_cached_tokens(payload, {})
        return None, None, None, cached_tokens

    prompt_tokens = usage.get("prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("input_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("promptTokenCount")
    if prompt_tokens is None:
        prompt_tokens = usage.get("total_input_tokens")

    completion_tokens = usage.get("completion_tokens")
    if completion_tokens is None:
        completion_tokens = usage.get("output_tokens")
    if completion_tokens is None:
        completion_tokens = usage.get("candidatesTokenCount")
    if completion_tokens is None:
        completion_tokens = usage.get("total_output_tokens")

    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = usage.get("totalTokenCount")
    if total_tokens is None and (
        isinstance(prompt_tokens, int) or isinstance(completion_tokens, int)
    ):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    prompt_tokens = _usage_int(prompt_tokens)
    completion_tokens = _usage_int(completion_tokens)
    total_tokens = _usage_int(total_tokens)
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    cached_tokens = _extract_cached_tokens(payload, usage)

    return prompt_tokens, completion_tokens, total_tokens, cached_tokens


def _extract_cached_tokens(payload: dict[str, Any], usage: dict[str, Any]) -> int | None:
    cached_tokens = usage.get("cache_read_input_tokens")
    if cached_tokens is None:
        cached_tokens = usage.get("cachedContentTokenCount")
    if cached_tokens is None:
        cached_tokens = usage.get("total_cached_tokens")
    if cached_tokens is None:
        cached_tokens = usage.get("cached_tokens")
    if cached_tokens is None:
        cached_tokens = usage.get("prompt_cache_hit_tokens")
    if cached_tokens is None:
        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            cached_tokens = prompt_details.get("cached_tokens")
    if cached_tokens is None:
        input_details = usage.get("input_tokens_details")
        if isinstance(input_details, dict):
            cached_tokens = input_details.get("cached_tokens")
    if cached_tokens is None:
        timings = payload.get("timings")
        if isinstance(timings, dict):
            cached_tokens = timings.get("cache_n")

    return _usage_int(cached_tokens)


async def write_request_log(metrics: RequestMetrics) -> None:
    async with SessionLocal() as session:
        log = RequestLog(
            request_id=metrics.request_id,
            trace_id=metrics.trace_id,
            model_alias=metrics.model_alias,
            endpoint_id=metrics.endpoint_id,
            api_key_id=metrics.api_key_id,
            requested_rule_group=metrics.requested_rule_group,
            rule_group=metrics.rule_group,
            prompt_tokens=metrics.prompt_tokens,
            completion_tokens=metrics.completion_tokens,
            total_tokens=metrics.total_tokens,
            cached_tokens=metrics.cached_tokens,
            is_cache_hit=bool((metrics.cached_tokens or 0) > 0),
            latency_ms=metrics.latency_ms,
            ttft_ms=metrics.ttft_ms,
            tps=metrics.tps,
            status_code=metrics.status_code,
            execution_mode=metrics.execution_mode,
            agent_node=metrics.agent_node,
            upstream_url=metrics.upstream_url,
        )
        session.add(log)

        tokens = metrics.total_tokens
        if tokens is None:
            tokens = (metrics.prompt_tokens or 0) + (metrics.completion_tokens or 0)
        today = app_today()
        await session.execute(
            update(APIKey)
            .where(APIKey.id == metrics.api_key_id)
            .values(
                used_today=case(
                    (
                        APIKey.used_today_date == today,
                        func.coalesce(APIKey.used_today, 0) + tokens,
                    ),
                    else_=tokens,
                ),
                used_today_date=today,
                total_usage=func.coalesce(APIKey.total_usage, 0) + tokens,
            )
        )

        await session.commit()


async def write_request_attempt_log(metrics: RequestAttemptMetrics) -> None:
    async with SessionLocal() as session:
        log = RequestAttemptLog(
            request_id=metrics.request_id,
            trace_id=metrics.trace_id,
            model_alias=metrics.model_alias,
            endpoint_id=metrics.endpoint_id,
            api_key_id=metrics.api_key_id,
            requested_rule_group=metrics.requested_rule_group,
            rule_group=metrics.rule_group,
            attempt_order=metrics.attempt_order,
            status_code=metrics.status_code,
            outcome=metrics.outcome,
            failure_reason=metrics.failure_reason,
            latency_ms=metrics.latency_ms,
            execution_mode=metrics.execution_mode,
            agent_node=metrics.agent_node,
            upstream_url=metrics.upstream_url,
        )
        session.add(log)
        await session.commit()

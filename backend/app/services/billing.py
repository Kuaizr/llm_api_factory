from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.db.models import APIKey, RequestAttemptLog, RequestLog
from app.db.session import SessionLocal


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


def extract_usage(payload: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    if not payload:
        return None, None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return None, None, None

    prompt_tokens = usage.get("prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("input_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("promptTokenCount")

    completion_tokens = usage.get("completion_tokens")
    if completion_tokens is None:
        completion_tokens = usage.get("output_tokens")
    if completion_tokens is None:
        completion_tokens = usage.get("candidatesTokenCount")

    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        total_tokens = usage.get("totalTokenCount")
    if total_tokens is None and (
        isinstance(prompt_tokens, int) or isinstance(completion_tokens, int)
    ):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    return prompt_tokens, completion_tokens, total_tokens


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
        api_key = await session.get(APIKey, metrics.api_key_id)
        if api_key is not None:
            today = datetime.now(timezone.utc).date()
            if api_key.used_today_date != today:
                api_key.used_today = 0
                api_key.used_today_date = today
            api_key.used_today = (api_key.used_today or 0) + tokens
            api_key.total_usage = (api_key.total_usage or 0) + tokens

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

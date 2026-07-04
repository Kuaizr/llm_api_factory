import time

from fastapi import HTTPException

from app.services.background_tasks import safe_create_task
from app.services.billing import RequestAttemptMetrics, write_request_attempt_log
from app.services.router import ModelRouter, RouteCandidate


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def record_attempt_log(
    *,
    request_id: str,
    trace_id: str,
    model_alias: str,
    candidate: RouteCandidate,
    requested_rule_group: str | None,
    rule_group: str,
    attempt_order: int,
    status_code: int | None,
    outcome: str,
    failure_reason: str | None,
    latency_ms: int,
    agent_node: str | None,
    upstream_url: str,
) -> None:
    metrics = RequestAttemptMetrics(
        request_id=request_id,
        trace_id=trace_id,
        model_alias=model_alias,
        endpoint_id=candidate.endpoint.id,
        api_key_id=candidate.api_key.id,
        requested_rule_group=requested_rule_group,
        rule_group=rule_group,
        attempt_order=attempt_order,
        status_code=status_code,
        outcome=outcome,
        failure_reason=failure_reason,
        latency_ms=latency_ms,
        execution_mode=candidate.execution_mode,
        agent_node=agent_node,
        upstream_url=upstream_url,
    )
    safe_create_task(write_request_attempt_log(metrics))


async def reserve_candidate_attempt_or_raise(
    *,
    router_service: ModelRouter,
    candidate: RouteCandidate,
    last_candidate: RouteCandidate,
    request_id: str,
    trace_id: str,
    model_alias: str,
    requested_rule_group: str | None,
    effective_group: str,
    attempt_order: int,
    attempt_start: float,
    agent_node: str | None,
    upstream_url: str,
) -> bool:
    if await router_service.reserve_candidate_attempt(candidate):
        return True
    record_attempt_log(
        request_id=request_id,
        trace_id=trace_id,
        model_alias=model_alias,
        candidate=candidate,
        requested_rule_group=requested_rule_group,
        rule_group=effective_group,
        attempt_order=attempt_order,
        status_code=None,
        outcome="fallback" if candidate != last_candidate else "error",
        failure_reason="rpm_limit",
        latency_ms=elapsed_ms(attempt_start),
        agent_node=agent_node,
        upstream_url=upstream_url,
    )
    if candidate != last_candidate:
        return False
    raise HTTPException(status_code=429, detail="API key rate limit exceeded")

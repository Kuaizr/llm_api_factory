import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import APIKey, Endpoint, RequestAttemptLog, RequestLog
from app.services import billing
from app.services.billing import (
    RequestAttemptMetrics,
    RequestMetrics,
    extract_usage,
    write_request_attempt_log,
    write_request_log,
)


def test_extract_usage_supports_standard_provider_shapes() -> None:
    assert extract_usage(
        {
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 1},
            }
        }
    ) == (2, 3, 5, 1)
    assert extract_usage({"usage": {"input_tokens": 4, "output_tokens": 6}}) == (
        4,
        6,
        10,
        None,
    )
    assert extract_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 8,
                "totalTokenCount": 15,
                "cachedContentTokenCount": 6,
            }
        }
    ) == (7, 8, 15, 6)
    assert extract_usage(
        {
            "usage": {
                "input_tokens": 11,
                "output_tokens": 12,
                "cache_read_input_tokens": 9,
            }
        }
    ) == (11, 12, 23, 9)
    assert extract_usage(
        {
            "metadata": {
                "total_usage": {
                    "total_input_tokens": 13,
                    "total_output_tokens": 14,
                    "total_cached_tokens": 10,
                }
            }
        }
    ) == (13, 14, 27, 10)
    assert extract_usage({"usage": "invalid"}) == (None, None, None, None)


@pytest.mark.asyncio
async def test_write_request_log_records_usage_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(billing, "SessionLocal", session_maker)

    async with session_maker() as session:
        endpoint = Endpoint(name="Billing", base_url="https://api.example.com")
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)
        api_key = APIKey(endpoint_id=endpoint.id, key="sk-billing", total_usage=10)
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)
        endpoint_id = endpoint.id
        api_key_id = api_key.id

    await write_request_log(
        RequestMetrics(
            request_id="req-billing",
            trace_id="trace-billing",
            model_alias="gpt-billing",
            endpoint_id=endpoint_id,
            api_key_id=api_key_id,
            requested_rule_group="requested",
            rule_group="effective",
            status_code=200,
            latency_ms=123,
            ttft_ms=10,
            tps=2.5,
            prompt_tokens=2,
            completion_tokens=3,
            total_tokens=None,
            cached_tokens=1,
            execution_mode="direct",
            upstream_url="https://api.example.com/v1/chat/completions",
        )
    )

    async with session_maker() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()
        api_key = await session.get(APIKey, api_key_id)

    assert log.request_id == "req-billing"
    assert log.total_tokens is None
    assert log.cached_tokens == 1
    assert log.is_cache_hit is True
    assert api_key is not None
    assert api_key.used_today == 5
    assert api_key.total_usage == 15

    await engine.dispose()


@pytest.mark.asyncio
async def test_write_request_attempt_log_records_fallback_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(billing, "SessionLocal", session_maker)

    async with session_maker() as session:
        endpoint = Endpoint(
            name="Attempt",
            base_url="https://api.example.com",
            access_mode="via_agent",
            agent_node="edge-hk",
        )
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)
        api_key = APIKey(endpoint_id=endpoint.id, key="sk-attempt")
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)
        endpoint_id = endpoint.id
        api_key_id = api_key.id

    await write_request_attempt_log(
        RequestAttemptMetrics(
            request_id="req-attempt",
            trace_id="trace-attempt",
            model_alias="gpt-attempt",
            endpoint_id=endpoint_id,
            api_key_id=api_key_id,
            requested_rule_group="requested",
            rule_group="effective",
            attempt_order=2,
            status_code=503,
            outcome="failure",
            failure_reason="upstream_status",
            latency_ms=456,
            execution_mode="via_agent",
            agent_node="edge-hk",
            upstream_url="https://api.example.com/v1/chat/completions",
        )
    )

    async with session_maker() as session:
        log = (await session.execute(select(RequestAttemptLog))).scalar_one()

    assert log.request_id == "req-attempt"
    assert log.trace_id == "trace-attempt"
    assert log.requested_rule_group == "requested"
    assert log.rule_group == "effective"
    assert log.attempt_order == 2
    assert log.status_code == 503
    assert log.outcome == "failure"
    assert log.failure_reason == "upstream_status"
    assert log.execution_mode == "via_agent"
    assert log.agent_node == "edge-hk"
    assert log.upstream_url == "https://api.example.com/v1/chat/completions"

    await engine.dispose()

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import APIKey, Endpoint, RequestLog
from app.services import billing
from app.services.billing import RequestMetrics, extract_usage, write_request_log


def test_extract_usage_supports_standard_provider_shapes() -> None:
    assert extract_usage(
        {"usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
    ) == (2, 3, 5)
    assert extract_usage({"usage": {"input_tokens": 4, "output_tokens": 6}}) == (
        4,
        6,
        10,
    )
    assert extract_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 8,
                "totalTokenCount": 15,
            }
        }
    ) == (7, 8, 15)
    assert extract_usage({"usage": "invalid"}) == (None, None, None)


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
            execution_mode="direct",
            upstream_url="https://api.example.com/v1/chat/completions",
        )
    )

    async with session_maker() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()
        api_key = await session.get(APIKey, api_key_id)

    assert log.request_id == "req-billing"
    assert log.total_tokens is None
    assert api_key is not None
    assert api_key.used_today == 5
    assert api_key.total_usage == 15

    await engine.dispose()

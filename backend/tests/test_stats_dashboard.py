from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.route_modules.stats_handlers import (
    admin_dump_search,
    admin_stats_distribution_groups,
    admin_stats_distribution_models,
    admin_stats_latency_percentiles,
    admin_stats_overview,
    admin_stats_timeseries,
    admin_stats_top_keys,
)
from app.db.models import APIKey, DumpIndex, Endpoint, RequestLog


@pytest.mark.asyncio
async def test_admin_stats_dashboard_handlers(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    endpoint = Endpoint(
        name="OpenAI",
        base_url="https://example.test/v1",
        provider="openai",
    )
    db_session.add(endpoint)
    await db_session.flush()
    api_key = APIKey(
        endpoint_id=endpoint.id,
        name="primary",
        key="sk-test",
        rule_group="gpt",
    )
    db_session.add(api_key)
    await db_session.flush()
    log = RequestLog(
        request_id="req-1",
        trace_id="trace-1",
        model_alias="gpt-5.5",
        endpoint_id=endpoint.id,
        api_key_id=api_key.id,
        requested_rule_group="gpt",
        rule_group="gpt",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        cached_tokens=80,
        is_cache_hit=True,
        latency_ms=250,
        ttft_ms=None,
        tps=None,
        status_code=200,
        execution_mode="direct",
        agent_node=None,
        upstream_url="https://example.test/v1/chat/completions",
        created_at=now,
    )
    dump = DumpIndex(
        request_id="req-1",
        trace_id="trace-1",
        model_alias="gpt-5.5",
        real_model="gpt-real",
        endpoint_id=endpoint.id,
        rule_group="gpt",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        cached_tokens=80,
        latency_ms=250,
        is_stream=False,
        is_cache_hit=True,
        stream_complete=True,
        previous_interaction_id=None,
        file_path="host/2026-07-06/gpt-real/req-1.json",
        hostname="host",
        created_at=now,
    )
    db_session.add_all([log, dump])
    await db_session.commit()

    since = (now - timedelta(minutes=5)).isoformat()
    until = (now + timedelta(minutes=5)).isoformat()

    overview = await admin_stats_overview(since=since, until=until, session=db_session)
    assert overview.total_requests.value == 1
    assert overview.total_tokens.value == 120
    assert overview.cache_hit_rate.value == 100
    assert overview.cached_tokens == 80

    timeseries = await admin_stats_timeseries(
        bucket_minutes=5, since=since, until=until, session=db_session
    )
    assert sum(bucket.request_count for bucket in timeseries) == 1
    assert sum(bucket.cache_hits for bucket in timeseries) == 1

    latency = await admin_stats_latency_percentiles(
        bucket_minutes=5, since=since, until=until, session=db_session
    )
    assert any(bucket.p95_ms == 250 for bucket in latency)

    models = await admin_stats_distribution_models(
        since=since, until=until, limit=12, session=db_session
    )
    assert models[0].name == "gpt-5.5"
    assert models[0].total_tokens == 120

    groups = await admin_stats_distribution_groups(
        since=since, until=until, limit=12, session=db_session
    )
    assert groups[0].name == "gpt"
    assert groups[0].request_count == 1

    top_keys = await admin_stats_top_keys(
        since=since, until=until, limit=10, session=db_session
    )
    assert top_keys[0].api_key_id == api_key.id
    assert top_keys[0].cache_hit_rate == 100
    assert top_keys[0].avg_latency_ms == 250

    dumps = await admin_dump_search(
        since=since,
        until=until,
        limit=50,
        offset=0,
        model=None,
        rule_group=None,
        status_code=None,
        trace_id=None,
        session=db_session,
    )
    assert dumps.total == 1
    assert dumps.items[0].trace_id == "trace-1"
    assert dumps.items[0].status_code == 200


@pytest.mark.asyncio
async def test_admin_stats_recent_logs_do_not_require_dump(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    endpoint = Endpoint(
        name="No Dump",
        base_url="https://example.test/v1",
        provider="openai",
    )
    db_session.add(endpoint)
    await db_session.flush()
    api_key = APIKey(endpoint_id=endpoint.id, key="sk-no-dump", rule_group="gpt")
    db_session.add(api_key)
    await db_session.flush()
    db_session.add(
        RequestLog(
            request_id="req-no-dump",
            trace_id="trace-no-dump",
            model_alias="gpt-5.5",
            endpoint_id=endpoint.id,
            api_key_id=api_key.id,
            requested_rule_group="gpt",
            rule_group="gpt",
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_tokens=40,
            is_cache_hit=True,
            latency_ms=250,
            ttft_ms=None,
            tps=None,
            status_code=200,
            execution_mode="direct",
            agent_node=None,
            upstream_url="https://example.test/v1/chat/completions",
            created_at=now,
        )
    )
    await db_session.commit()

    since = (now - timedelta(minutes=5)).isoformat()
    until = (now + timedelta(minutes=5)).isoformat()

    overview = await admin_stats_overview(since=since, until=until, session=db_session)
    assert overview.total_requests.value == 1
    assert overview.cache_hit_rate.value == 100
    assert overview.cached_tokens == 40

    dumps = await admin_dump_search(
        since=since,
        until=until,
        limit=50,
        offset=0,
        model=None,
        rule_group=None,
        status_code=None,
        trace_id=None,
        session=db_session,
    )
    assert dumps.total == 1
    assert dumps.items[0].request_id == "req-no-dump"
    assert dumps.items[0].is_cache_hit is True
    assert dumps.items[0].file_path is None
    assert dumps.items[0].hostname is None

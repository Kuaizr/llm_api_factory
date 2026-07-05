import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import route_helpers
from app.core.config import Settings
from app.db.base import Base
from app.db.models import DumpIndex, RoutingRule


@pytest.mark.asyncio
async def test_dump_proxy_record_writes_partitioned_file_and_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(route_helpers, "SessionLocal", session_maker)
    monkeypatch.setattr(route_helpers, "DUMP_HOSTNAME", "test-host.local")
    monkeypatch.setattr(
        route_helpers,
        "get_settings",
        lambda: Settings(proxy_dump_root=tmp_path.as_posix()),
    )
    rule = RoutingRule(
        id=7,
        model_pattern=".*",
        group_name="alpha",
        priority=1,
        is_active=True,
        dump_enabled=True,
        dump_path="captures",
        target_key_ids_json="{}",
    )

    await route_helpers._dump_proxy_record(
        rule,
        "req-1",
        "trace-1",
        "Endpoint",
        "gpt-alias",
        b'{"model":"gpt-alias","previous_interaction_id":"prev-123"}',
        b'data: {"text":"ok"}\n\n',
        200,
        endpoint_id=42,
        real_model="gpt-5/chat",
        prompt_tokens=None,
        completion_tokens=3,
        total_tokens=None,
        cached_tokens=2,
        latency_ms=123,
        is_stream=True,
        stream_complete=False,
        session_id="session/abc",
        request_path="/openai/v1/chat/completions",
    )

    async with session_maker() as session:
        row = (await session.execute(select(DumpIndex))).scalar_one()

    assert row.request_id == "req-1"
    assert row.trace_id == "trace-1"
    assert row.model_alias == "gpt-alias"
    assert row.real_model == "gpt-5/chat"
    assert row.endpoint_id == 42
    assert row.rule_group == "alpha"
    assert row.prompt_tokens is None
    assert row.completion_tokens == 3
    assert row.total_tokens is None
    assert row.cached_tokens == 2
    assert row.latency_ms == 123
    assert row.is_stream is True
    assert row.is_cache_hit is True
    assert row.stream_complete is False
    assert row.previous_interaction_id == "prev-123"
    assert row.hostname == "test-host.local"
    assert row.file_path.startswith("test-host.local/")
    assert row.file_path.endswith("/gpt-5_chat/req-1.json")

    dump_file = tmp_path / "captures" / row.file_path
    payload = json.loads(dump_file.read_text(encoding="utf-8"))
    assert payload["file_path"] == row.file_path
    assert payload["stream_complete"] is False
    assert payload["prompt_tokens"] is None
    assert payload["cached_tokens"] == 2
    assert payload["is_cache_hit"] is True
    assert payload["previous_interaction_id"] == "prev-123"
    assert payload["real_model"] == "gpt-5/chat"

    session_file = (
        tmp_path / "captures" / "test-host.local" / "sessions" / "session_abc.jsonl"
    )
    session_rows = [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]
    assert session_rows[0]["request_id"] == "req-1"

    await engine.dispose()

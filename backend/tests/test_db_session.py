import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.models import APIKey, Endpoint, ModelMap
from app.db import session as db_session
from app.db.session import create_database_engine


@pytest.mark.asyncio
async def test_sqlite_engine_enables_foreign_key_checks() -> None:
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA foreign_keys"))
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_file_engine_uses_wal_and_busy_timeout(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "factory.db"
    monkeypatch.setattr(db_session.settings, "sqlite_busy_timeout_ms", 1234)
    monkeypatch.setattr(db_session.settings, "sqlite_journal_mode", "WAL")
    engine = create_database_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        async with engine.connect() as conn:
            busy_timeout = await conn.execute(text("PRAGMA busy_timeout"))
            journal_mode = await conn.execute(text("PRAGMA journal_mode"))

            assert busy_timeout.scalar_one() == 1234
            assert journal_mode.scalar_one().lower() == "wal"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_engine_uses_configured_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_session.settings, "pg_pool_size", 3)
    monkeypatch.setattr(db_session.settings, "pg_max_overflow", 2)
    engine = create_database_engine("postgresql+asyncpg://llm:password@localhost/test")
    try:
        pool = engine.sync_engine.pool
        assert pool.size() == 3
        assert pool._max_overflow == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_endpoint_delete_cascades_child_rows() -> None:
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, expire_on_commit=False)
        async with session_maker() as session:
            endpoint = Endpoint(name="openai", base_url="https://api.example.com")
            session.add(endpoint)
            await session.flush()
            session.add_all(
                [
                    APIKey(endpoint_id=endpoint.id, key="sk-test"),
                    ModelMap(
                        endpoint_id=endpoint.id,
                        model_alias="gpt-5",
                        real_model="gpt-5",
                    ),
                ]
            )
            await session.commit()

        async with session_maker() as session:
            await session.execute(delete(Endpoint).where(Endpoint.name == "openai"))
            await session.commit()

        async with session_maker() as session:
            key_count = await session.scalar(select(func.count(APIKey.id)))
            map_count = await session.scalar(select(func.count(ModelMap.id)))
            assert key_count == 0
            assert map_count == 0
    finally:
        await engine.dispose()

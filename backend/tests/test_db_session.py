import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.models import APIKey, Endpoint, ModelMap
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

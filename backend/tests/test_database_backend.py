import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import APIKey, Endpoint


@pytest.mark.asyncio
async def test_database_backend_applies_schema_and_supports_basic_crud(
    db_session: AsyncSession,
) -> None:
    migration_ids = (
        await db_session.execute(text("SELECT migration_id FROM schema_migrations"))
    ).scalars().all()
    assert "20260705_legacy_schema_updates" in migration_ids
    assert "20260705_audit_logs" in migration_ids

    endpoint_name = f"pg-smoke-{uuid.uuid4().hex}"
    endpoint = Endpoint(
        name=endpoint_name,
        base_url="https://api.example.com/v1",
        provider="openai",
    )
    db_session.add(endpoint)
    await db_session.flush()

    api_key = APIKey(endpoint_id=endpoint.id, key="sk-test")
    db_session.add(api_key)
    await db_session.commit()

    loaded_endpoint = await db_session.scalar(
        select(Endpoint).where(Endpoint.name == endpoint_name)
    )
    assert loaded_endpoint is not None

    await db_session.execute(delete(Endpoint).where(Endpoint.id == endpoint.id))
    await db_session.commit()

    remaining_keys = await db_session.scalar(
        select(func.count(APIKey.id)).where(APIKey.endpoint_id == endpoint.id)
    )
    assert remaining_keys == 0

import json
import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import migrations
from app.db.models import APIKey, Endpoint, RoutingRule


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


@pytest.mark.asyncio
async def test_postgresql_exposure_migration_normalizes_invalid_legacy_json(
    db_session: AsyncSession,
) -> None:
    if db_session.bind is None or db_session.bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL-specific migration test")

    prefix = f"pg-migration-{uuid.uuid4().hex}"
    rules = [
        RoutingRule(
            model_pattern=".*",
            group_name=f"{prefix}-blank",
            priority=10,
            is_active=True,
            target_key_ids_json="",
        ),
        RoutingRule(
            model_pattern=".*",
            group_name=f"{prefix}-invalid",
            priority=10,
            is_active=True,
            target_key_ids_json="not-json",
        ),
        RoutingRule(
            model_pattern=".*",
            group_name=f"{prefix}-scalar",
            priority=10,
            is_active=True,
            target_key_ids_json="42",
        ),
    ]
    db_session.add_all(rules)
    await db_session.commit()

    migration = next(
        item
        for item in migrations.SCHEMA_MIGRATIONS
        if item.migration_id == "20260719_routing_rule_exposure_formats"
    )
    for statement in migrations._migration_statements(migration, "postgresql"):
        await db_session.execute(text(statement))
    await db_session.commit()
    db_session.expire_all()

    loaded_rules = (
        await db_session.execute(
            select(RoutingRule)
            .where(RoutingRule.group_name.like(f"{prefix}%"))
            .order_by(RoutingRule.id)
        )
    ).scalars().all()
    configs = [json.loads(rule.target_key_ids_json) for rule in loaded_rules]
    assert len(configs) == 3
    assert all(config["target_key_ids"] == [] for config in configs)
    assert all("codex" in config["exposure_formats"] for config in configs)

    await db_session.execute(
        delete(RoutingRule).where(RoutingRule.group_name.like(f"{prefix}%"))
    )
    await db_session.commit()

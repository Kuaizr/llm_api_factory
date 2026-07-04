import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import migrations


@pytest.mark.asyncio
async def test_schema_migrations_are_recorded_and_not_reapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def noop_encrypt_existing_secret_rows(_conn) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(migrations, "_encrypt_existing_secret_rows", noop_encrypt_existing_secret_rows)
    monkeypatch.setattr(
        migrations,
        "SCHEMA_MIGRATIONS",
        (
            migrations.SchemaMigration(
                migration_id="test_once",
                statements=(
                    "CREATE TABLE migration_probe (id INTEGER PRIMARY KEY, value INTEGER)",
                    "INSERT INTO migration_probe (id, value) VALUES (1, 42)",
                ),
            ),
        ),
    )

    await migrations.apply_schema_updates(engine)
    await migrations.apply_schema_updates(engine)

    async with engine.connect() as conn:
        migration_ids = (
            await conn.execute(text("SELECT migration_id FROM schema_migrations"))
        ).scalars().all()
        probe_values = (
            await conn.execute(text("SELECT value FROM migration_probe"))
        ).scalars().all()

    assert migration_ids == ["test_once"]
    assert probe_values == [42]

    await engine.dispose()


@pytest.mark.asyncio
async def test_request_attempt_log_composite_index_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def noop_encrypt_existing_secret_rows(_conn) -> None:  # noqa: ANN001
        return None

    migration = next(
        item
        for item in migrations.SCHEMA_MIGRATIONS
        if item.migration_id == "20260705_request_attempt_log_composite_indexes"
    )
    monkeypatch.setattr(migrations, "_encrypt_existing_secret_rows", noop_encrypt_existing_secret_rows)
    monkeypatch.setattr(migrations, "SCHEMA_MIGRATIONS", (migration,))

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE request_attempt_logs (
                    id INTEGER PRIMARY KEY,
                    model_alias VARCHAR(128),
                    endpoint_id INTEGER,
                    api_key_id INTEGER,
                    outcome VARCHAR(32),
                    created_at DATETIME
                )
                """
            )
        )

    await migrations.apply_schema_updates(engine)

    async with engine.connect() as conn:
        index_rows = await conn.execute(text("PRAGMA index_list(request_attempt_logs)"))
        index_names = {row[1] for row in index_rows}

    assert "ix_request_attempt_logs_model_alias_created_at" in index_names
    assert "ix_request_attempt_logs_endpoint_id_created_at" in index_names
    assert "ix_request_attempt_logs_api_key_id_created_at" in index_names
    assert "ix_request_attempt_logs_outcome_created_at" in index_names

    await engine.dispose()

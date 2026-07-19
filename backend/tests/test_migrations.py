import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import migrations
from app.services.access_keys import access_key_preview, hash_access_key, is_hashed_access_key


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


def test_schema_migration_table_uses_postgresql_timestamp() -> None:
    sql = migrations._schema_migrations_table_sql("postgresql")

    assert "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL" in sql
    assert "DATETIME" not in sql


def test_migration_statements_are_dialect_specific() -> None:
    migration = migrations.SchemaMigration(
        migration_id="dialect_specific",
        statements=("SELECT 1",),
        sqlite_only=("SELECT 'sqlite'",),
        pg_only=("SELECT 'pg'",),
    )

    assert migrations._migration_statements(migration, "sqlite") == (
        "SELECT 1",
        "SELECT 'sqlite'",
    )
    assert migrations._migration_statements(migration, "postgresql") == (
        "SELECT 1",
        "SELECT 'pg'",
    )


def test_historical_sqlite_migrations_do_not_run_on_postgresql() -> None:
    pg_statements = {
        migration.migration_id: migrations._migration_statements(migration, "postgresql")
        for migration in migrations.SCHEMA_MIGRATIONS
    }
    exposure_formats_statements = pg_statements.pop(
        "20260719_routing_rule_exposure_formats"
    )

    assert pg_statements == {
        "20260705_legacy_schema_updates": (),
        "20260705_audit_logs": (),
        "20260705_request_attempt_log_composite_indexes": (),
        "20260705_hash_factory_access_keys": (),
        "20260705_dump_index_cache_and_interactions": (
            "ALTER TABLE dump_index ADD COLUMN cached_tokens INTEGER",
            "ALTER TABLE dump_index ADD COLUMN previous_interaction_id VARCHAR(128)",
            "CREATE INDEX IF NOT EXISTS ix_dump_prev_interaction ON dump_index(previous_interaction_id)",
        ),
        "20260706_request_log_cache_metadata": (
            "ALTER TABLE request_logs ADD COLUMN cached_tokens INTEGER",
            "ALTER TABLE request_logs ADD COLUMN is_cache_hit BOOLEAN DEFAULT FALSE",
        ),
        "20260719_request_log_exposure_format": (
            "ALTER TABLE request_logs ADD COLUMN exposure_format VARCHAR(32)",
            "ALTER TABLE request_attempt_logs ADD COLUMN exposure_format VARCHAR(32)",
            "CREATE INDEX IF NOT EXISTS ix_request_logs_exposure_format ON request_logs(exposure_format)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_exposure_format ON request_attempt_logs(exposure_format)",
        ),
    }
    assert len(exposure_formats_statements) == 3
    assert all(
        "exposure_formats" in statement
        for statement in exposure_formats_statements[:2]
    )


@pytest.mark.asyncio
async def test_sqlite_only_migration_runs_on_sqlite(
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
                migration_id="sqlite_only",
                sqlite_only=(
                    "CREATE TABLE dialect_sqlite_probe (id INTEGER PRIMARY KEY, value INTEGER)",
                    "INSERT INTO dialect_sqlite_probe (id, value) VALUES (1, 7)",
                ),
                pg_only=("CREATE TABLE pg_probe (id INTEGER PRIMARY KEY)",),
            ),
        ),
    )

    await migrations.apply_schema_updates(engine)

    async with engine.connect() as conn:
        probe_values = (
            await conn.execute(text("SELECT value FROM dialect_sqlite_probe"))
        ).scalars().all()

    assert probe_values == [7]

    await engine.dispose()


@pytest.mark.asyncio
async def test_routing_rule_exposure_formats_sql_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def noop_encrypt_existing_secret_rows(_conn) -> None:  # noqa: ANN001
        return None

    migration = next(
        item
        for item in migrations.SCHEMA_MIGRATIONS
        if item.migration_id == "20260719_routing_rule_exposure_formats"
    )
    monkeypatch.setattr(migrations, "_encrypt_existing_secret_rows", noop_encrypt_existing_secret_rows)
    monkeypatch.setattr(migrations, "SCHEMA_MIGRATIONS", (migration,))

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE routing_rules (
                    id INTEGER PRIMARY KEY,
                    model_pattern VARCHAR(128),
                    group_name VARCHAR(64),
                    is_active BOOLEAN,
                    target_key_ids_json TEXT
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO routing_rules (
                    id, model_pattern, group_name, is_active, target_key_ids_json
                ) VALUES
                    (1, 'gpt-.*', 'default', 0, :default_config),
                    (2, 'gpt-.*', 'codex', 1, :codex_config),
                    (3, '.*', 'legacy-all', 1, :any_config)
                """
            ),
            {
                "default_config": json.dumps(
                    {
                        "target_key_ids": [1],
                        "strategy": "sequential",
                        "exposure_format": "any",
                    }
                ),
                "codex_config": json.dumps(
                    {
                        "target_key_ids": [2],
                        "strategy": "sequential",
                        "exposure_format": "codex",
                    }
                ),
                "any_config": json.dumps(
                    {
                        "target_key_ids": [3],
                        "strategy": "weighted_round_robin",
                        "exposure_format": "any",
                    }
                ),
            },
        )

    await migrations.apply_schema_updates(engine)

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, model_pattern, is_active, target_key_ids_json
                    FROM routing_rules
                    ORDER BY id
                    """
                )
            )
        ).mappings().all()

    all_formats = ["chat", "response", "codex", "message", "claude_code", "gemini"]
    configs = {row["id"]: json.loads(row["target_key_ids_json"]) for row in rows}
    assert configs[1]["target_key_ids"] == []
    assert configs[1]["exposure_formats"] == all_formats
    assert rows[0]["model_pattern"] == ".*"
    assert rows[0]["is_active"] == 1
    assert configs[2]["exposure_formats"] == ["codex"]
    assert configs[3]["exposure_formats"] == all_formats
    assert all("exposure_format" not in config for config in configs.values())

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


@pytest.mark.asyncio
async def test_schema_update_hashes_existing_factory_access_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def noop_encrypt_existing_secret_rows(_conn) -> None:  # noqa: ANN001
        return None

    migration = next(
        item
        for item in migrations.SCHEMA_MIGRATIONS
        if item.migration_id == "20260705_hash_factory_access_keys"
    )
    monkeypatch.setattr(migrations, "_encrypt_existing_secret_rows", noop_encrypt_existing_secret_rows)
    monkeypatch.setattr(migrations, "SCHEMA_MIGRATIONS", (migration,))

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE factory_access_keys (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(128),
                    key VARCHAR(128) NOT NULL UNIQUE,
                    rule_groups_json TEXT DEFAULT '[]',
                    is_active BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO factory_access_keys (id, name, key, rule_groups_json, is_active)
                VALUES (1, 'client', 'rk-plain', '["default"]', 1)
                """
            )
        )

    await migrations.apply_schema_updates(engine)

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT key, key_preview FROM factory_access_keys WHERE id = 1")
            )
        ).mappings().one()

    assert row["key"] == hash_access_key("rk-plain")
    assert is_hashed_access_key(row["key"])
    assert row["key_preview"] == access_key_preview("rk-plain")

    await engine.dispose()

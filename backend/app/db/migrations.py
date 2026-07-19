from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.services.access_keys import (
    access_key_preview,
    hash_access_key,
    is_hashed_access_key,
)
from app.services.secrets import (
    encrypt_oauth_config_if_possible,
    encrypt_secret_value_if_possible,
    encryption_available,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchemaMigration:
    migration_id: str
    statements: tuple[str, ...] = ()
    sqlite_only: tuple[str, ...] = ()
    pg_only: tuple[str, ...] = ()


def _schema_migrations_table_sql(dialect_name: str) -> str:
    applied_at_type = "TIMESTAMP" if dialect_name == "postgresql" else "DATETIME"
    return f"""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id VARCHAR(128) PRIMARY KEY,
        applied_at {applied_at_type} DEFAULT CURRENT_TIMESTAMP NOT NULL
    )
    """


def _migration_statements(
    migration: SchemaMigration,
    dialect_name: str,
) -> tuple[str, ...]:
    if dialect_name == "postgresql":
        dialect_specific = migration.pg_only
    elif dialect_name == "sqlite":
        dialect_specific = migration.sqlite_only
    else:
        dialect_specific = ()
    return (*migration.statements, *dialect_specific)


SCHEMA_MIGRATIONS: tuple[SchemaMigration, ...] = (
    SchemaMigration(
        migration_id="20260705_legacy_schema_updates",
        sqlite_only=(
            "ALTER TABLE endpoints ADD COLUMN url_path_suffix VARCHAR(256)",
            "ALTER TABLE endpoints ADD COLUMN extra_headers TEXT",
            "ALTER TABLE endpoints ADD COLUMN extra_cookies TEXT",
            "ALTER TABLE endpoints ADD COLUMN extra_query_params TEXT",
            "ALTER TABLE endpoints ADD COLUMN oauth_config TEXT",
            "ALTER TABLE endpoints ADD COLUMN request_body_template TEXT",
            "ALTER TABLE endpoints ADD COLUMN probe_interval_seconds INTEGER",
            "ALTER TABLE endpoints ADD COLUMN access_mode VARCHAR(32) DEFAULT 'direct'",
            """
            UPDATE endpoints
            SET access_mode = 'via_agent'
            WHERE agent_node IS NOT NULL
              AND TRIM(agent_node) != ''
              AND (access_mode IS NULL OR TRIM(access_mode) = '' OR access_mode = 'direct')
            """,
            "ALTER TABLE api_keys ADD COLUMN rule_groups_json TEXT",
            "ALTER TABLE agents ADD COLUMN network_group VARCHAR(128)",
            "ALTER TABLE agents ADD COLUMN labels_json TEXT",
            "ALTER TABLE agents ADD COLUMN is_draining BOOLEAN DEFAULT 0",
            """
            UPDATE agents
            SET is_draining = 0
            WHERE is_draining IS NULL
            """,
            "ALTER TABLE request_logs ADD COLUMN requested_rule_group VARCHAR(64)",
            "ALTER TABLE request_logs ADD COLUMN execution_mode VARCHAR(32)",
            "ALTER TABLE request_logs ADD COLUMN agent_node VARCHAR(128)",
            "ALTER TABLE request_logs ADD COLUMN upstream_url VARCHAR(1024)",
            """
            UPDATE request_logs
            SET execution_mode = 'direct'
            WHERE execution_mode IS NULL OR TRIM(execution_mode) = ''
            """,
            "ALTER TABLE model_maps ADD COLUMN probe_managed BOOLEAN DEFAULT 0",
            "ALTER TABLE routing_rules ADD COLUMN dump_enabled BOOLEAN DEFAULT 0",
            "ALTER TABLE routing_rules ADD COLUMN dump_path VARCHAR(1024)",
            """
            INSERT INTO routing_rules (
                model_pattern,
                group_name,
                priority,
                is_active,
                dump_enabled,
                target_key_ids_json
            )
            SELECT
                '.*',
                'default',
                0,
                1,
                0,
                '{"target_key_ids": [], "strategy": "weighted_round_robin"}'
            WHERE NOT EXISTS (
                SELECT 1 FROM routing_rules WHERE group_name = 'default'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS factory_access_keys (
                id INTEGER PRIMARY KEY,
                name VARCHAR(128),
                key VARCHAR(128) NOT NULL UNIQUE,
                key_preview VARCHAR(64),
                rule_groups_json TEXT DEFAULT '[]',
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_factory_access_keys_key ON factory_access_keys(key)",
            """
            INSERT INTO factory_access_keys (name, key, rule_groups_json, is_active, created_at)
            SELECT
                old.name,
                old.key,
                json_array(rr.group_name),
                old.is_active,
                old.created_at
            FROM rule_access_keys AS old
            JOIN routing_rules AS rr ON rr.id = old.rule_id
            WHERE NOT EXISTS (SELECT 1 FROM factory_access_keys WHERE key = old.key)
            """,
            "DROP TABLE IF EXISTS rule_access_keys",
            """
            UPDATE api_keys
            SET rule_groups_json = '["default"]'
            WHERE (rule_groups_json IS NULL OR TRIM(rule_groups_json) = '')
              AND (rule_group IS NULL OR TRIM(rule_group) = '' OR LOWER(rule_group) = 'default')
            """,
            """
            UPDATE api_keys
            SET rule_groups_json = '["default","' || REPLACE(rule_group, '"', '') || '"]'
            WHERE (rule_groups_json IS NULL OR TRIM(rule_groups_json) = '')
              AND rule_group IS NOT NULL
              AND TRIM(rule_group) != ''
              AND LOWER(rule_group) != 'default'
            """,
            """
            CREATE TABLE IF NOT EXISTS request_attempt_logs (
                id INTEGER PRIMARY KEY,
                request_id VARCHAR(64) NOT NULL,
                trace_id VARCHAR(64) NOT NULL,
                model_alias VARCHAR(128) NOT NULL,
                endpoint_id INTEGER NOT NULL,
                api_key_id INTEGER NOT NULL,
                requested_rule_group VARCHAR(64),
                rule_group VARCHAR(64),
                attempt_order INTEGER NOT NULL,
                status_code INTEGER,
                outcome VARCHAR(32) NOT NULL,
                failure_reason VARCHAR(128),
                latency_ms INTEGER NOT NULL,
                execution_mode VARCHAR(32),
                agent_node VARCHAR(128),
                upstream_url VARCHAR(1024),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(endpoint_id) REFERENCES endpoints(id),
                FOREIGN KEY(api_key_id) REFERENCES api_keys(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_request_id ON request_attempt_logs(request_id)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_trace_id ON request_attempt_logs(trace_id)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_model_alias ON request_attempt_logs(model_alias)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_rule_group ON request_attempt_logs(rule_group)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_outcome ON request_attempt_logs(outcome)",
            "CREATE INDEX IF NOT EXISTS ix_request_logs_model_alias_created_at ON request_logs(model_alias, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_request_logs_endpoint_id_created_at ON request_logs(endpoint_id, created_at)",
        ),
    ),
    SchemaMigration(
        migration_id="20260705_audit_logs",
        sqlite_only=(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY,
                actor VARCHAR(128) DEFAULT 'admin' NOT NULL,
                action VARCHAR(64) NOT NULL,
                resource_type VARCHAR(64) NOT NULL,
                resource_id VARCHAR(128),
                resource_name VARCHAR(256),
                before_json TEXT,
                after_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_actor ON audit_logs(actor)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs(action)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_resource_type ON audit_logs(resource_type)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_resource_created_at ON audit_logs(resource_type, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_action_created_at ON audit_logs(action, created_at)",
        ),
    ),
    SchemaMigration(
        migration_id="20260705_request_attempt_log_composite_indexes",
        sqlite_only=(
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_model_alias_created_at ON request_attempt_logs(model_alias, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_endpoint_id_created_at ON request_attempt_logs(endpoint_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_api_key_id_created_at ON request_attempt_logs(api_key_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_outcome_created_at ON request_attempt_logs(outcome, created_at)",
        ),
    ),
    SchemaMigration(
        migration_id="20260705_hash_factory_access_keys",
        sqlite_only=(
            "ALTER TABLE factory_access_keys ADD COLUMN key_preview VARCHAR(64)",
        ),
    ),
    SchemaMigration(
        migration_id="20260705_dump_index_cache_and_interactions",
        statements=(
            "ALTER TABLE dump_index ADD COLUMN cached_tokens INTEGER",
            "ALTER TABLE dump_index ADD COLUMN previous_interaction_id VARCHAR(128)",
            "CREATE INDEX IF NOT EXISTS ix_dump_prev_interaction ON dump_index(previous_interaction_id)",
        ),
    ),
    SchemaMigration(
        migration_id="20260706_request_log_cache_metadata",
        statements=(
            "ALTER TABLE request_logs ADD COLUMN cached_tokens INTEGER",
        ),
        sqlite_only=(
            "ALTER TABLE request_logs ADD COLUMN is_cache_hit BOOLEAN DEFAULT 0",
        ),
        pg_only=(
            "ALTER TABLE request_logs ADD COLUMN is_cache_hit BOOLEAN DEFAULT FALSE",
        ),
    ),
    SchemaMigration(
        migration_id="20260719_request_log_exposure_format",
        statements=(
            "ALTER TABLE request_logs ADD COLUMN exposure_format VARCHAR(32)",
            "ALTER TABLE request_attempt_logs ADD COLUMN exposure_format VARCHAR(32)",
            "CREATE INDEX IF NOT EXISTS ix_request_logs_exposure_format ON request_logs(exposure_format)",
            "CREATE INDEX IF NOT EXISTS ix_request_attempt_logs_exposure_format ON request_attempt_logs(exposure_format)",
        ),
    ),
    SchemaMigration(
        migration_id="20260719_routing_rule_exposure_formats",
        sqlite_only=(
            """
            UPDATE routing_rules
            SET target_key_ids_json = json_object(
                'target_key_ids', json('[]'),
                'strategy', 'weighted_round_robin',
                'exposure_formats', json('["chat","response","codex","message","claude_code","gemini"]')
            )
            WHERE target_key_ids_json IS NULL
               OR TRIM(target_key_ids_json) = ''
               OR NOT json_valid(target_key_ids_json)
            """,
            """
            UPDATE routing_rules
            SET target_key_ids_json = json_object(
                'target_key_ids', json(target_key_ids_json),
                'strategy', 'weighted_round_robin',
                'exposure_formats', json('["chat","response","codex","message","claude_code","gemini"]')
            )
            WHERE json_type(target_key_ids_json) = 'array'
            """,
            """
            UPDATE routing_rules
            SET target_key_ids_json = json_remove(
                json_set(
                    target_key_ids_json,
                    '$.exposure_formats',
                    json(
                        CASE
                            WHEN LOWER(group_name) = 'default' THEN
                                '["chat","response","codex","message","claude_code","gemini"]'
                            WHEN json_type(target_key_ids_json, '$.exposure_formats') = 'array' THEN
                                json_extract(target_key_ids_json, '$.exposure_formats')
                            WHEN COALESCE(json_extract(target_key_ids_json, '$.exposure_format'), 'any') = 'any' THEN
                                '["chat","response","codex","message","claude_code","gemini"]'
                            ELSE json_array(json_extract(target_key_ids_json, '$.exposure_format'))
                        END
                    )
                ),
                '$.exposure_format'
            )
            WHERE json_type(target_key_ids_json) = 'object'
            """,
            """
            UPDATE routing_rules
            SET model_pattern = '.*',
                is_active = 1,
                target_key_ids_json = json_set(
                    target_key_ids_json,
                    '$.target_key_ids',
                    json('[]')
                )
            WHERE LOWER(group_name) = 'default'
            """,
        ),
        pg_only=(
            """
            UPDATE routing_rules
            SET target_key_ids_json = jsonb_build_object(
                'target_key_ids', '[]'::jsonb,
                'strategy', 'weighted_round_robin',
                'exposure_formats', '["chat","response","codex","message","claude_code","gemini"]'::jsonb
            )::text
            WHERE target_key_ids_json IS NULL
               OR BTRIM(target_key_ids_json) = ''
               OR NOT pg_input_is_valid(target_key_ids_json, 'jsonb')
            """,
            """
            UPDATE routing_rules
            SET target_key_ids_json = jsonb_build_object(
                'target_key_ids', '[]'::jsonb,
                'strategy', 'weighted_round_robin',
                'exposure_formats', '["chat","response","codex","message","claude_code","gemini"]'::jsonb
            )::text
            WHERE jsonb_typeof(target_key_ids_json::jsonb) NOT IN ('array', 'object')
            """,
            """
            UPDATE routing_rules
            SET target_key_ids_json = jsonb_build_object(
                'target_key_ids', target_key_ids_json::jsonb,
                'strategy', 'weighted_round_robin',
                'exposure_formats', '["chat","response","codex","message","claude_code","gemini"]'::jsonb
            )::text
            WHERE jsonb_typeof(target_key_ids_json::jsonb) = 'array'
            """,
            """
            UPDATE routing_rules
            SET target_key_ids_json = (
                jsonb_set(
                    target_key_ids_json::jsonb,
                    '{exposure_formats}',
                    CASE
                        WHEN LOWER(group_name) = 'default' THEN
                            '["chat","response","codex","message","claude_code","gemini"]'::jsonb
                        WHEN jsonb_typeof(target_key_ids_json::jsonb->'exposure_formats') = 'array' THEN
                            target_key_ids_json::jsonb->'exposure_formats'
                        WHEN COALESCE(target_key_ids_json::jsonb->>'exposure_format', 'any') = 'any' THEN
                            '["chat","response","codex","message","claude_code","gemini"]'::jsonb
                        ELSE jsonb_build_array(target_key_ids_json::jsonb->>'exposure_format')
                    END,
                    true
                ) - 'exposure_format'
            )::text
            WHERE jsonb_typeof(target_key_ids_json::jsonb) = 'object'
            """,
            """
            UPDATE routing_rules
            SET model_pattern = '.*',
                is_active = TRUE,
                target_key_ids_json = jsonb_set(
                    target_key_ids_json::jsonb,
                    '{target_key_ids}',
                    '[]'::jsonb,
                    true
                )::text
            WHERE LOWER(group_name) = 'default'
            """,
        ),
    ),
)


def _is_ignorable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    ignored_tokens = (
        "duplicate column name",
        "duplicate column",
        "already exists",
        "duplicate key",
        "already an index",
        "no such table: factory_access_keys",
        "no such table: rule_access_keys",
    )
    return any(token in message for token in ignored_tokens)


async def apply_schema_updates(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        dialect_name = conn.dialect.name
        await _ensure_migration_table(conn)
        for migration in SCHEMA_MIGRATIONS:
            if await _migration_applied(conn, migration.migration_id):
                continue
            for statement in _migration_statements(migration, dialect_name):
                try:
                    async with conn.begin_nested():
                        await conn.execute(text(statement))
                except (OperationalError, ProgrammingError) as exc:
                    if _is_ignorable_error(exc):
                        continue
                    raise
            await _record_migration(conn, migration.migration_id)
        await _hash_existing_factory_access_key_rows(conn)
        await _encrypt_existing_secret_rows(conn)


async def _ensure_migration_table(conn) -> None:  # noqa: ANN001
    await conn.execute(text(_schema_migrations_table_sql(conn.dialect.name)))


async def _migration_applied(conn, migration_id: str) -> bool:  # noqa: ANN001
    result = await conn.execute(
        text(
            """
            SELECT 1
            FROM schema_migrations
            WHERE migration_id = :migration_id
            """
        ),
        {"migration_id": migration_id},
    )
    return result.first() is not None


async def _record_migration(conn, migration_id: str) -> None:  # noqa: ANN001
    await conn.execute(
        text(
            """
            INSERT INTO schema_migrations (migration_id)
            VALUES (:migration_id)
            """
        ),
        {"migration_id": migration_id},
    )


async def _table_exists(conn, table_name: str) -> bool:  # noqa: ANN001
    return await conn.run_sync(
        lambda sync_conn: inspect(sync_conn).has_table(table_name)
    )


async def _hash_existing_factory_access_key_rows(conn) -> None:  # noqa: ANN001
    if not await _table_exists(conn, "factory_access_keys"):
        return
    try:
        rows = (
            await conn.execute(
                text("SELECT id, key, key_preview FROM factory_access_keys WHERE key IS NOT NULL")
            )
        ).mappings().all()
    except (OperationalError, ProgrammingError) as exc:
        if _is_ignorable_error(exc):
            return
        raise

    for row in rows:
        stored_key = str(row["key"] or "")
        stored_preview = row["key_preview"]
        if is_hashed_access_key(stored_key):
            continue
        preview = str(stored_preview or "").strip() or access_key_preview(stored_key)
        await conn.execute(
            text(
                """
                UPDATE factory_access_keys
                SET key = :key, key_preview = :key_preview
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "key": hash_access_key(stored_key),
                "key_preview": preview,
            },
        )


async def _encrypt_existing_secret_rows(conn) -> None:  # noqa: ANN001
    settings = get_settings()
    if not encryption_available(settings):
        logger.warning(
            "Secret encryption key not configured; existing API keys remain unencrypted"
        )
        return

    api_key_rows = []
    if await _table_exists(conn, "api_keys"):
        try:
            api_key_rows = (
                await conn.execute(text("SELECT id, key FROM api_keys WHERE key IS NOT NULL"))
            ).mappings().all()
        except (OperationalError, ProgrammingError) as exc:
            if _is_ignorable_error(exc):
                api_key_rows = []
            else:
                raise

    for row in api_key_rows:
        encrypted = encrypt_secret_value_if_possible(row["key"], settings=settings)
        if encrypted and encrypted != row["key"]:
            await conn.execute(
                text("UPDATE api_keys SET key = :key WHERE id = :id"),
                {"id": row["id"], "key": encrypted},
            )

    endpoint_rows = []
    if await _table_exists(conn, "endpoints"):
        try:
            endpoint_rows = (
                await conn.execute(
                    text("SELECT id, oauth_config FROM endpoints WHERE oauth_config IS NOT NULL")
                )
            ).mappings().all()
        except (OperationalError, ProgrammingError) as exc:
            if _is_ignorable_error(exc):
                endpoint_rows = []
            else:
                raise

    for row in endpoint_rows:
        try:
            parsed = json.loads(row["oauth_config"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        encrypted_config = encrypt_oauth_config_if_possible(parsed, settings=settings)
        if encrypted_config is None:
            continue
        serialized = json.dumps(encrypted_config, ensure_ascii=False)
        if serialized != row["oauth_config"]:
            await conn.execute(
                text("UPDATE endpoints SET oauth_config = :oauth_config WHERE id = :id"),
                {"id": row["id"], "oauth_config": serialized},
            )

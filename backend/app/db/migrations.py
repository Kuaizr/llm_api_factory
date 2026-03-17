from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine


def _is_ignorable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    ignored_tokens = (
        "duplicate column name",
        "already exists",
        "duplicate key",
        "already an index",
        "relation",
    )
    return any(token in message for token in ignored_tokens)


async def apply_schema_updates(engine: AsyncEngine) -> None:
    statements = [
        # Endpoint 扩展字段（通用 Provider 支持）
        "ALTER TABLE endpoints ADD COLUMN url_path_suffix VARCHAR(256)",
        "ALTER TABLE endpoints ADD COLUMN extra_headers TEXT",
        "ALTER TABLE endpoints ADD COLUMN extra_cookies TEXT",
        "ALTER TABLE endpoints ADD COLUMN extra_query_params TEXT",
        "ALTER TABLE endpoints ADD COLUMN oauth_config TEXT",
        "ALTER TABLE endpoints ADD COLUMN request_body_template TEXT",
        "ALTER TABLE endpoints ADD COLUMN probe_interval_seconds INTEGER",
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
        CREATE TABLE IF NOT EXISTS rule_access_keys (
            id INTEGER PRIMARY KEY,
            rule_id INTEGER NOT NULL,
            name VARCHAR(128),
            key VARCHAR(128) NOT NULL UNIQUE,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(rule_id) REFERENCES routing_rules(id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_rule_access_keys_rule_id ON rule_access_keys(rule_id)",
        "CREATE INDEX IF NOT EXISTS ix_rule_access_keys_key ON rule_access_keys(key)",
    ]

    async with engine.begin() as conn:
        for statement in statements:
            try:
                await conn.execute(text(statement))
            except (OperationalError, ProgrammingError) as exc:
                if _is_ignorable_error(exc):
                    continue
                raise

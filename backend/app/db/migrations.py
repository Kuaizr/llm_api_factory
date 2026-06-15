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
        "no such table: rule_access_keys",
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
        # 新建 factory_access_keys 表
        """
        CREATE TABLE IF NOT EXISTS factory_access_keys (
            id INTEGER PRIMARY KEY,
            name VARCHAR(128),
            key VARCHAR(128) NOT NULL UNIQUE,
            rule_groups_json TEXT DEFAULT '[]',
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_factory_access_keys_key ON factory_access_keys(key)",
        # 迁移旧 rule_access_keys 数据到 factory_access_keys
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
        # 清理旧表
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
    ]

    async with engine.begin() as conn:
        for statement in statements:
            try:
                await conn.execute(text(statement))
            except (OperationalError, ProgrammingError) as exc:
                if _is_ignorable_error(exc):
                    continue
                raise

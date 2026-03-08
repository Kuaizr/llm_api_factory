from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def main() -> None:
    db_path = Path(__file__).resolve().parent / "llm_api_factory.db"
    if not db_path.exists():
        raise SystemExit(f"Database file not found: {db_path}")

    with sqlite3.connect(db_path.as_posix()) as conn:
        cursor = conn.cursor()
        if not _column_exists(cursor, "request_logs", "ttft_ms"):
            cursor.execute("ALTER TABLE request_logs ADD COLUMN ttft_ms INTEGER")
        if not _column_exists(cursor, "request_logs", "tps"):
            cursor.execute("ALTER TABLE request_logs ADD COLUMN tps FLOAT")
        if not _column_exists(cursor, "request_logs", "rule_group"):
            cursor.execute("ALTER TABLE request_logs ADD COLUMN rule_group TEXT")
        cursor.execute(
            """
            UPDATE request_logs
            SET rule_group = COALESCE(
                (SELECT rule_group FROM api_keys WHERE api_keys.id = request_logs.api_key_id),
                'default'
            )
            WHERE rule_group IS NULL
            """
        )
        if not _column_exists(cursor, "api_keys", "used_today_date"):
            cursor.execute("ALTER TABLE api_keys ADD COLUMN used_today_date TEXT")
        if not _column_exists(cursor, "agents", "auth_token_hash"):
            cursor.execute("ALTER TABLE agents ADD COLUMN auth_token_hash TEXT")
        if not _column_exists(cursor, "agents", "supports_gpt"):
            cursor.execute("ALTER TABLE agents ADD COLUMN supports_gpt BOOLEAN")
        if not _column_exists(cursor, "agents", "supports_gemini"):
            cursor.execute("ALTER TABLE agents ADD COLUMN supports_gemini BOOLEAN")
        if not _column_exists(cursor, "agents", "supports_claude"):
            cursor.execute("ALTER TABLE agents ADD COLUMN supports_claude BOOLEAN")
        if not _column_exists(cursor, "agents", "probe_latency_ms"):
            cursor.execute("ALTER TABLE agents ADD COLUMN probe_latency_ms INTEGER")
        if not _column_exists(cursor, "agents", "probe_checked_at"):
            cursor.execute("ALTER TABLE agents ADD COLUMN probe_checked_at TEXT")
        today = datetime.now(timezone.utc).date().isoformat()
        cursor.execute(
            "UPDATE api_keys SET used_today_date = ? WHERE used_today_date IS NULL",
            (today,),
        )
        conn.commit()

    print("request_logs schema updated")


if __name__ == "__main__":
    main()

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()
SQLITE_JOURNAL_MODES = {"delete", "truncate", "persist", "memory", "wal", "off"}
POSTGRESQL_SCHEMES = ("postgresql", "postgres")


def _normalize_sqlite_journal_mode(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized not in SQLITE_JOURNAL_MODES:
        return None
    return normalized


def _configure_sqlite_connection(
    dbapi_connection,  # noqa: ANN001
    _connection_record,  # noqa: ANN001
    *,
    busy_timeout_ms: int,
    journal_mode: str | None,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        if busy_timeout_ms > 0:
            cursor.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        normalized_journal_mode = _normalize_sqlite_journal_mode(journal_mode)
        if normalized_journal_mode:
            cursor.execute(f"PRAGMA journal_mode={normalized_journal_mode}")
    finally:
        cursor.close()


def create_database_engine(database_url: str) -> AsyncEngine:
    engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if database_url.startswith(POSTGRESQL_SCHEMES):
        engine_kwargs.update(
            {
                "pool_size": max(1, int(settings.pg_pool_size)),
                "max_overflow": max(0, int(settings.pg_max_overflow)),
            }
        )
    db_engine = create_async_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite"):
        event.listen(
            db_engine.sync_engine,
            "connect",
            lambda dbapi_connection, connection_record: _configure_sqlite_connection(
                dbapi_connection,
                connection_record,
                busy_timeout_ms=max(0, int(settings.sqlite_busy_timeout_ms)),
                journal_mode=settings.sqlite_journal_mode,
            ),
        )
    return db_engine


engine = create_database_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session

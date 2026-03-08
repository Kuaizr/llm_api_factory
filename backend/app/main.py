import asyncio
from contextlib import suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes import router as v1_router
from app.core.config import get_settings
from app.core.http_client import close_http_client
from app.core.logging import setup_logging
from app.core.redis import close_redis
from app.db.base import Base
from app.db.session import engine
from app.services.health_monitor import HealthMonitor

settings = get_settings()


def _parse_origins(raw: str) -> list[str]:
    if raw.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="LLM API Factory")
origins = _parse_origins(settings.cors_allow_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\\d+)?$",
    allow_credentials="*" not in origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(v1_router)


@app.on_event("startup")
async def on_startup() -> None:
    setup_logging()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if settings.health_probe_enabled:
        monitor = HealthMonitor()
        app.state.health_monitor = monitor
        app.state.health_task = asyncio.create_task(monitor.run())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    monitor = getattr(app.state, "health_monitor", None)
    task = getattr(app.state, "health_task", None)
    if monitor:
        await monitor.stop()
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    await close_http_client()
    await close_redis()

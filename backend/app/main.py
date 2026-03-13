import asyncio
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


def _frontend_dist_dir() -> Path:
    # backend/app/main.py -> backend -> repo root -> frontend/dist
    return (Path(__file__).resolve().parents[1] / ".." / "frontend" / "dist").resolve()


FRONTEND_DIST_DIR = _frontend_dist_dir()
FRONTEND_ASSETS_DIR = FRONTEND_DIST_DIR / "assets"
FRONTEND_INDEX_FILE = FRONTEND_DIST_DIR / "index.html"
RESERVED_API_PREFIXES = ("v1/", "admin/", "agent/", "auth/")

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

if FRONTEND_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS_DIR), name="assets")


@app.get("/", include_in_schema=False)
async def serve_frontend_index() -> FileResponse:
    if FRONTEND_INDEX_FILE.exists():
        return FileResponse(FRONTEND_INDEX_FILE)
    raise HTTPException(
        status_code=404,
        detail="Frontend dist not found, run `npm run build` in frontend",
    )


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend_fallback(full_path: str) -> FileResponse:
    # Keep API routes handled by router; fallback only serves SPA files.
    if any(full_path.startswith(prefix) for prefix in RESERVED_API_PREFIXES):
        raise HTTPException(status_code=404, detail="Not Found")

    if not FRONTEND_DIST_DIR.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend dist not found, run `npm run build` in frontend",
        )

    target_file = (FRONTEND_DIST_DIR / full_path).resolve()
    if not target_file.is_relative_to(FRONTEND_DIST_DIR):
        raise HTTPException(status_code=404, detail="Not Found")

    if target_file.is_file():
        return FileResponse(target_file)

    if FRONTEND_INDEX_FILE.exists():
        return FileResponse(FRONTEND_INDEX_FILE)

    raise HTTPException(
        status_code=404,
        detail="Frontend dist not found, run `npm run build` in frontend",
    )


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

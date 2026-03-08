from httpx import AsyncClient, Timeout

from app.core.config import get_settings

_http_client: AsyncClient | None = None


def _build_timeout() -> Timeout:
    settings = get_settings()
    return Timeout(settings.http_timeout_seconds)


async def get_http_client() -> AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = AsyncClient(timeout=_build_timeout())
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

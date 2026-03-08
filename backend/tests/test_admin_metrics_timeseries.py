from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session


@dataclass
class RequestLogStub:
    created_at: datetime
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int


class FakeResult:
    def __init__(self, rows: list[RequestLogStub]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[RequestLogStub]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[RequestLogStub]) -> None:
        self._rows = rows

    async def execute(self, stmt) -> FakeResult:  # noqa: ANN001
        return FakeResult(self._rows)


@pytest.mark.asyncio
async def test_metrics_timeseries_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    logs = [
        RequestLogStub(
            created_at=start,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            latency_ms=120,
        ),
        RequestLogStub(
            created_at=start.replace(hour=1),
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            latency_ms=200,
        ),
    ]
    session = FakeSession(logs)
    settings = Settings(master_auth_token="token")

    async def override_session():
        yield session

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/admin/metrics/timeseries?bucket_minutes=60&since=2024-01-01T00:00:00Z&until=2024-01-01T02:00:00Z",
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 3
    assert payload[0]["request_count"] == 1
    assert payload[1]["total_tokens"] == 30
    assert payload[2]["request_count"] == 0

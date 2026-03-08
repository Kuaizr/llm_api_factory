import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session


async def override_session():
    yield None


@pytest.mark.asyncio
async def test_auth_login_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/auth/login", json={"password": "token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] == "token"
    assert payload["role"] == "admin"
    assert payload["issued_at"]


@pytest.mark.asyncio
async def test_auth_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/auth/login", json={"password": "bad"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/me", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "admin"
    assert payload["is_admin"] is True


@pytest.mark.asyncio
async def test_auth_me_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/me", headers={"Authorization": "Bearer bad"})

    assert response.status_code == 401

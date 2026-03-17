import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.api.v1 import routes_legacy as legacy_module
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


@pytest.mark.asyncio
async def test_auth_me_with_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/me", headers={"x-api-key": "token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "admin"
    assert payload["is_admin"] is True


@pytest.mark.asyncio
async def test_auth_password_update_success(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        update_response = await client.post(
            "/auth/password",
            headers={"Authorization": "Bearer token"},
            json={"current_password": "token", "new_password": "next-token"},
        )
        old_login = await client.post("/auth/login", json={"password": "token"})
        new_login = await client.post("/auth/login", json={"password": "next-token"})

    assert update_response.status_code == 200
    assert update_response.json()["token"] == "next-token"
    assert old_login.status_code == 401
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_auth_password_update_validates_current_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(master_auth_token="token")
    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/password",
            headers={"Authorization": "Bearer token"},
            json={"current_password": "wrong", "new_password": "next-token"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect"


def test_routes_compat_modules_no_legacy_module() -> None:
    module_names = {module.__name__ for module in routes_module._COMPAT_MODULES}
    assert "app.api.v1.route_helpers" in module_names
    assert "app.api.v1.routes_legacy" not in module_names


def test_routes_legacy_exports_require_master_auth() -> None:
    assert legacy_module._require_master_auth is routes_module._require_master_auth


def test_routes_legacy_exports_proxy_helper() -> None:
    assert legacy_module._inspect_stream_chunk is routes_module._inspect_stream_chunk


def test_routes_legacy_setattr_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = object()
    monkeypatch.setattr(legacy_module, "_require_master_auth", marker)
    assert routes_module._require_master_auth is marker


def test_extract_factory_api_key_prefers_bearer() -> None:
    assert routes_module._extract_factory_api_key({"Authorization": "Bearer token-a"}) == "token-a"
    assert routes_module._extract_factory_api_key({"x-api-key": "token-b"}) == "token-b"
    assert routes_module._extract_factory_api_key({"X-API-Key": " token-c "}) == "token-c"
    assert (
        routes_module._extract_factory_api_key(
            {"Authorization": "Bearer token-a", "x-api-key": "token-b"}
        )
        == "token-a"
    )

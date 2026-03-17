from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.api.v1.route_helpers import _extract_factory_api_key
from app.api.v1.route_models import (
    AuthLoginRequest,
    AuthLoginResponse,
    AuthMeResponse,
    AuthPasswordUpdateRequest,
    AuthPasswordUpdateResponse,
)
from app.core.config import get_settings


def _is_master_authorized(request: Request) -> bool:
    settings = get_settings()
    if not settings.master_auth_token:
        return True
    token = _extract_factory_api_key(request.headers)
    return token == settings.master_auth_token


async def auth_login(payload: AuthLoginRequest) -> AuthLoginResponse:
    settings = get_settings()
    if not settings.master_auth_token or payload.password != settings.master_auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return AuthLoginResponse(
        token=settings.master_auth_token,
        role="admin",
        issued_at=datetime.now(timezone.utc),
    )


async def auth_me(request: Request) -> AuthMeResponse:
    if not _is_master_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return AuthMeResponse(role="admin", is_admin=True)


async def auth_update_password(
    payload: AuthPasswordUpdateRequest,
    request: Request,
) -> AuthPasswordUpdateResponse:
    settings = get_settings()
    current_password = settings.master_auth_token
    if not current_password:
        raise HTTPException(status_code=400, detail="Master auth is disabled")
    if not _is_master_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if payload.current_password != current_password:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_password = payload.new_password.strip()
    if len(new_password) < 4:
        raise HTTPException(status_code=400, detail="New password is too short")

    settings.master_auth_token = new_password
    return AuthPasswordUpdateResponse(
        token=new_password,
        updated_at=datetime.now(timezone.utc),
    )

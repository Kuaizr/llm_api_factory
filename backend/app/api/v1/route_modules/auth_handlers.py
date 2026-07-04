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
from app.services.admin_auth import (
    issue_admin_session_token,
    verify_admin_session_token,
    verify_master_password,
)


def _is_master_authorized(request: Request) -> bool:
    settings = get_settings()
    if not settings.master_auth_token:
        return True
    token = _extract_factory_api_key(request.headers)
    return verify_admin_session_token(token, settings)


async def auth_login(payload: AuthLoginRequest) -> AuthLoginResponse:
    settings = get_settings()
    if not verify_master_password(payload.password, settings):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return AuthLoginResponse(
        token=issue_admin_session_token(settings),
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
    if not verify_master_password(payload.current_password, settings):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_password = payload.new_password.strip()
    if len(new_password) < 4:
        raise HTTPException(status_code=400, detail="New password is too short")

    settings.master_auth_token = new_password
    return AuthPasswordUpdateResponse(
        token=issue_admin_session_token(settings),
        updated_at=datetime.now(timezone.utc),
    )

from fastapi import APIRouter

from app.api.v1.route_models import (
    AuthLoginResponse,
    AuthMeResponse,
    AuthPasswordUpdateResponse,
)
from app.api.v1.route_modules.auth_handlers import auth_login, auth_me, auth_update_password

router = APIRouter()

router.add_api_route(
    "/auth/login",
    auth_login,
    methods=["POST"],
    response_model=AuthLoginResponse,
)
router.add_api_route(
    "/auth/me",
    auth_me,
    methods=["GET"],
    response_model=AuthMeResponse,
)
router.add_api_route(
    "/auth/password",
    auth_update_password,
    methods=["POST"],
    response_model=AuthPasswordUpdateResponse,
)

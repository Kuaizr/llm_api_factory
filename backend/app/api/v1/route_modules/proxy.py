from fastapi import APIRouter

from app.api.v1.route_modules.proxy_handlers import (
    anthropic_passthrough,
    openai_passthrough,
)

router = APIRouter()

router.add_api_route(
    "/openai/v1/{path:path}",
    openai_passthrough,
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
router.add_api_route(
    "/anthropic/v1/{path:path}",
    anthropic_passthrough,
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)

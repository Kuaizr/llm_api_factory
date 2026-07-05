import sys
from types import ModuleType

from fastapi import APIRouter

from app.api.v1 import route_helpers, route_proxy_helpers
from app.api.v1.route_modules import (
    admin_handlers,
    agent_handlers,
    auth_handlers,
    health_handlers,
    proxy_agent_handler,
    proxy_agent_streams,
    proxy_attempts,
    proxy_core,
    proxy_direct_handler,
    proxy_entrypoints,
    proxy_models,
    proxy_trace,
    proxy_handlers,
    stats_handlers,
)
from app.api.v1.route_modules.admin import router as admin_router
from app.api.v1.route_modules.agent import router as agent_router
from app.api.v1.route_modules.auth import router as auth_router
from app.api.v1.route_modules.health import router as health_router
from app.api.v1.route_modules.proxy import router as proxy_router
from app.api.v1.route_modules.stats import router as stats_router


_COMPAT_MODULES = (
    route_helpers,
    route_proxy_helpers,
    admin_handlers,
    agent_handlers,
    auth_handlers,
    health_handlers,
    proxy_agent_handler,
    proxy_agent_streams,
    proxy_attempts,
    proxy_core,
    proxy_direct_handler,
    proxy_entrypoints,
    proxy_models,
    proxy_trace,
    proxy_handlers,
    stats_handlers,
)


class _LegacyCompatModule(ModuleType):
    def __getattr__(self, name: str):
        for module in _COMPAT_MODULES:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        for module in _COMPAT_MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _LegacyCompatModule

router = APIRouter()

router.include_router(proxy_router)
router.include_router(auth_router)
router.include_router(agent_router)
router.include_router(stats_router)
router.include_router(health_router)
router.include_router(admin_router)

import sys
from types import ModuleType

from app.api.v1 import route_helpers, route_proxy_helpers
from app.api.v1.route_modules import (
    admin_handlers,
    agent_handlers,
    auth_handlers,
    health_handlers,
    proxy_handlers,
    stats_handlers,
)

_COMPAT_MODULES = (
    route_helpers,
    route_proxy_helpers,
    admin_handlers,
    agent_handlers,
    auth_handlers,
    health_handlers,
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

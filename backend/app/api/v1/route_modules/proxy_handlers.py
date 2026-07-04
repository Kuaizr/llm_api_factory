"""Thin compatibility layer for proxy route handlers.

The proxy execution core lives in ``proxy_core``. This module keeps the
historical import path used by route registration and legacy tests.
"""

from app.api.v1.route_modules import proxy_core as _proxy_core
from app.api.v1.route_modules import proxy_entrypoints as _proxy_entrypoints
from app.api.v1.route_modules.proxy_core import *  # noqa: F401,F403
from app.api.v1.route_modules.proxy_entrypoints import *  # noqa: F401,F403


def __getattr__(name: str):
    if hasattr(_proxy_entrypoints, name):
        return getattr(_proxy_entrypoints, name)
    return getattr(_proxy_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_proxy_entrypoints)) | set(dir(_proxy_core)))

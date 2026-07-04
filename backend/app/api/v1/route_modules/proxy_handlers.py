"""Thin compatibility layer for proxy route handlers.

The proxy execution core lives in ``proxy_core``. This module keeps the
historical import path used by route registration and legacy tests.
"""

from app.api.v1.route_modules import proxy_core as _proxy_core
from app.api.v1.route_modules.proxy_core import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_proxy_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_proxy_core)))

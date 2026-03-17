from importlib import import_module


_LAZY_EXPORTS = {
    "admin_router": ("app.api.v1.route_modules.admin", "router"),
    "agent_router": ("app.api.v1.route_modules.agent", "router"),
    "auth_router": ("app.api.v1.route_modules.auth", "router"),
    "health_router": ("app.api.v1.route_modules.health", "router"),
    "proxy_router": ("app.api.v1.route_modules.proxy", "router"),
    "stats_router": ("app.api.v1.route_modules.stats", "router"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, symbol_name = target
    module = import_module(module_name)
    return getattr(module, symbol_name)


__all__ = list(_LAZY_EXPORTS.keys())

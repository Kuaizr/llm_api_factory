from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.session import get_session
from app.services.router import RouteCandidate
from conftest import TestMemoryRedis as MemoryRedis


@dataclass
class EndpointStub:
    id: int
    name: str
    base_url: str
    provider: str = "openai"
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    agent_node: str | None = None
    oauth_config: str | None = None
    request_body_template: str | None = None
    extra_headers: str | None = None
    extra_cookies: str | None = None
    extra_query_params: str | None = None
    url_path_suffix: str | None = None


@dataclass
class APIKeyStub:
    id: int
    key: str
    weight: int = 1


class FakeSession:
    async def execute(self, stmt):  # noqa: ANN001
        raise AssertionError("Database should not be used in proxy route tests")


def build_proxy_app(
    monkeypatch: pytest.MonkeyPatch,
    candidate_or_candidates: RouteCandidate | list[RouteCandidate],
    upstream_client: httpx.AsyncClient,
    recorded: dict,
    *,
    agent_manager: object | None = None,
) -> FastAPI:
    settings = Settings(master_auth_token="token", admin_legacy_master_bearer_enabled=True)
    redis = MemoryRedis()
    candidates = (
        list(candidate_or_candidates)
        if isinstance(candidate_or_candidates, list)
        else [candidate_or_candidates]
    )

    async def fake_get_redis():
        return redis

    async def override_session():
        yield FakeSession()

    async def fake_get_candidates(  # noqa: ANN001
        self,
        session,
        model_alias: str,
        rule_group: str,
        **kwargs,
    ):
        recorded["model_alias"] = model_alias
        recorded["rule_group"] = rule_group
        recorded["candidate_kwargs"] = kwargs
        available_candidates = [
            candidate
            for candidate in candidates
            if redis.store.get(f"circuit:{candidate.api_key.id}:state") != "open"
        ]
        provider_filters = kwargs.get("provider_filters")
        fallback_to_any = bool(kwargs.get("provider_filter_fallback_to_any"))
        if isinstance(provider_filters, str):
            filters = {provider_filters.strip().lower()}
        elif provider_filters:
            filters = {str(item).strip().lower() for item in provider_filters}
        else:
            filters = set()
        if filters:
            filtered = [
                candidate
                for candidate in available_candidates
                if (
                    str(getattr(candidate.endpoint, "provider", "openai") or "openai")
                    .strip()
                    .lower()
                    in filters
                )
            ]
            if filtered or not fallback_to_any:
                return filtered, rule_group
        return available_candidates, rule_group

    async def fake_get_http_client() -> httpx.AsyncClient:
        return upstream_client

    async def fake_write_request_log(metrics):  # noqa: ANN001
        recorded["metrics"] = metrics

    async def fake_write_request_attempt_log(metrics):  # noqa: ANN001
        recorded.setdefault("attempts", []).append(metrics)

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(routes_module, "get_notifier", lambda: None)
    monkeypatch.setattr(routes_module.ModelRouter, "get_candidates", fake_get_candidates)
    monkeypatch.setattr(routes_module, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(routes_module, "write_request_log", fake_write_request_log)
    monkeypatch.setattr(
        routes_module, "write_request_attempt_log", fake_write_request_attempt_log
    )
    recorded["redis"] = redis
    if agent_manager is not None:
        monkeypatch.setattr(routes_module, "get_agent_manager", lambda: agent_manager)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session
    return app

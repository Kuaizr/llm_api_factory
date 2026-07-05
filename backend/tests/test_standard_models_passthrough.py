import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import routes as routes_module
from app.core.config import Settings
from app.db.models import APIKey, Endpoint, FactoryAccessKey, ModelMap, RoutingRule
from app.db.session import get_session
from app.services.access_keys import access_key_preview, hash_access_key
from conftest import TestMemoryRedis as MemoryRedis


async def _add_factory_key(
    session: AsyncSession,
    key: str,
    groups: list[str],
) -> None:
    factory_key = FactoryAccessKey(
        name="models-key",
        key=hash_access_key(key),
        key_preview=access_key_preview(key),
        is_active=True,
    )
    factory_key.rule_groups = groups
    session.add(factory_key)
    await session.commit()


async def _add_endpoint_key_and_models(
    session: AsyncSession,
    *,
    endpoint_name: str,
    provider: str,
    base_url: str,
    key_name: str,
    upstream_key: str,
    models: list[tuple[str, str]],
) -> APIKey:
    endpoint = Endpoint(
        name=endpoint_name,
        base_url=base_url,
        provider=provider,
        auth_header_name="x-goog-api-key" if provider == "gemini" else "Authorization",
        auth_header_prefix="" if provider == "gemini" else "Bearer",
        is_active=True,
    )
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)

    api_key = APIKey(
        endpoint_id=endpoint.id,
        name=key_name,
        key=upstream_key,
        is_active=True,
    )
    api_key.assign_rule_groups(["default"])
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    for alias, real_model in models:
        session.add(
            ModelMap(
                endpoint_id=endpoint.id,
                model_alias=alias,
                real_model=real_model,
            )
        )
    await session.commit()
    return api_key


async def _add_rule(
    session: AsyncSession,
    *,
    group: str,
    pattern: str,
    target_key_ids: list[int],
) -> None:
    session.add(
        RoutingRule(
            model_pattern=pattern,
            group_name=group,
            priority=10,
            is_active=True,
            target_key_ids_json=json.dumps(
                {"target_key_ids": target_key_ids, "strategy": "sequential"}
            ),
        )
    )
    await session.commit()


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    upstream_client: httpx.AsyncClient,
) -> FastAPI:
    settings = Settings(master_auth_token="admin")
    redis = MemoryRedis()

    async def fake_get_redis():
        return redis

    async def fake_get_http_client() -> httpx.AsyncClient:
        return upstream_client

    async def override_session():
        yield session

    monkeypatch.setattr(routes_module, "get_settings", lambda: settings)
    monkeypatch.setattr(routes_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(routes_module, "get_notifier", lambda: None)
    monkeypatch.setattr(routes_module, "get_http_client", fake_get_http_client)

    app = FastAPI()
    app.include_router(routes_module.router)
    app.dependency_overrides[get_session] = override_session
    return app


@pytest.mark.asyncio
async def test_openai_models_passthrough_filters_by_factory_key_rule_group_union(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    await _add_factory_key(db_session, "fk-models", ["alpha", "beta"])
    alpha_key = await _add_endpoint_key_and_models(
        db_session,
        endpoint_name="OpenAI-A",
        provider="openai",
        base_url="https://openai-a.example.com/v1",
        key_name="alpha-key",
        upstream_key="sk-alpha",
        models=[("gpt-alpha", "gpt-4.1")],
    )
    beta_key = await _add_endpoint_key_and_models(
        db_session,
        endpoint_name="OpenAI-B",
        provider="openai",
        base_url="https://openai-b.example.com/v1",
        key_name="beta-key",
        upstream_key="sk-beta",
        models=[("gpt-beta", "gpt-4.2")],
    )
    await _add_rule(db_session, group="alpha", pattern="gpt-alpha", target_key_ids=[alpha_key.id])
    await _add_rule(db_session, group="beta", pattern="gpt-beta", target_key_ids=[beta_key.id])

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4.1", "object": "model", "owned_by": "openai", "extra": "keep"},
                    {"id": "gpt-4.2", "object": "model", "owned_by": "openai", "extra": "keep"},
                    {"id": "gpt-private", "object": "model", "owned_by": "openai"},
                ],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = _build_app(monkeypatch, db_session, upstream_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/openai/v1/models",
            headers={"Authorization": "Bearer fk-models"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["data"]] == ["gpt-4.1", "gpt-4.2"]
    assert payload["data"][0]["extra"] == "keep"
    assert {request.url.path for request in requests} == {"/v1/models"}


@pytest.mark.asyncio
async def test_gemini_models_passthrough_preserves_native_fields_and_filters_pages(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    await _add_factory_key(db_session, "fk-gemini-models", ["gemini-a", "gemini-b"])
    api_key = await _add_endpoint_key_and_models(
        db_session,
        endpoint_name="Gemini",
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        key_name="gemini-key",
        upstream_key="gemini-upstream",
        models=[
            ("gemini-alias-a", "gemini-2.5-flash"),
            ("gemini-alias-b", "gemini-3.5-flash"),
        ],
    )
    await _add_rule(
        db_session,
        group="gemini-a",
        pattern="gemini-alias-a",
        target_key_ids=[api_key.id],
    )
    await _add_rule(
        db_session,
        group="gemini-b",
        pattern="gemini-alias-b",
        target_key_ids=[api_key.id],
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("pageToken") == "next":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-3.5-flash",
                            "version": "3.5",
                            "displayName": "Gemini 3.5 Flash",
                            "inputTokenLimit": 1048576,
                            "thinking": True,
                        },
                        {
                            "name": "models/gemini-private",
                            "version": "private",
                            "displayName": "Private",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "version": "001",
                        "displayName": "Gemini 2.5 Flash",
                        "description": "native field must stay",
                        "inputTokenLimit": 1048576,
                        "outputTokenLimit": 65536,
                        "supportedGenerationMethods": ["generateContent", "countTokens"],
                    }
                ],
                "nextPageToken": "next",
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = _build_app(monkeypatch, db_session, upstream_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/gemini/v1beta/models?key=fk-gemini-models",
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload["models"]] == [
        "models/gemini-2.5-flash",
        "models/gemini-3.5-flash",
    ]
    assert payload["models"][0]["description"] == "native field must stay"
    assert payload["models"][1]["thinking"] is True
    assert "nextPageToken" not in payload
    assert [request.url.path for request in requests] == ["/v1beta/models", "/v1beta/models"]
    assert "key" not in requests[0].url.params
    assert requests[1].url.params["pageToken"] == "next"
    assert "key" not in requests[1].url.params


@pytest.mark.asyncio
async def test_anthropic_models_passthrough_filters_native_data(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    await _add_factory_key(db_session, "fk-claude-models", ["claude"])
    api_key = await _add_endpoint_key_and_models(
        db_session,
        endpoint_name="Claude",
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        key_name="claude-key",
        upstream_key="sk-claude",
        models=[("claude-alias", "claude-opus-4-8")],
    )
    await _add_rule(
        db_session,
        group="claude",
        pattern="claude-alias",
        target_key_ids=[api_key.id],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "claude-opus-4-8",
                        "type": "model",
                        "display_name": "Claude Opus 4.8",
                    },
                    {"id": "claude-private", "type": "model"},
                ],
                "has_more": False,
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = _build_app(monkeypatch, db_session, upstream_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/anthropic/v1/models",
            headers={"x-api-key": "fk-claude-models"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == [
        {
            "id": "claude-opus-4-8",
            "type": "model",
            "display_name": "Claude Opus 4.8",
        }
    ]
    assert payload["has_more"] is False


@pytest.mark.asyncio
async def test_models_passthrough_does_not_fallback_unmatched_rule_group_to_default(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    await _add_factory_key(db_session, "fk-no-default-leak", ["alpha", "beta"])
    alpha_key = await _add_endpoint_key_and_models(
        db_session,
        endpoint_name="OpenAI-Alpha",
        provider="openai",
        base_url="https://openai-alpha.example.com/v1",
        key_name="alpha-key",
        upstream_key="sk-alpha",
        models=[("gpt-alpha", "gpt-alpha-real")],
    )
    default_key = await _add_endpoint_key_and_models(
        db_session,
        endpoint_name="OpenAI-Default",
        provider="openai",
        base_url="https://openai-default.example.com/v1",
        key_name="default-key",
        upstream_key="sk-default",
        models=[("gpt-default", "gpt-default-real")],
    )
    await _add_rule(
        db_session,
        group="alpha",
        pattern="gpt-alpha",
        target_key_ids=[alpha_key.id],
    )
    await _add_rule(
        db_session,
        group="default",
        pattern=".*",
        target_key_ids=[default_key.id],
    )

    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(str(request.url.host))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-alpha-real", "object": "model"},
                    {"id": "gpt-default-real", "object": "model"},
                ],
            },
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = _build_app(monkeypatch, db_session, upstream_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/openai/v1/models",
            headers={"Authorization": "Bearer fk-no-default-leak"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["data"]] == ["gpt-alpha-real"]
    assert requested_hosts == ["openai-alpha.example.com"]

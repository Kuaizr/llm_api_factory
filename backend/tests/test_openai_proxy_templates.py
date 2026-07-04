import json

import httpx
import pytest

from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


@pytest.mark.asyncio
async def test_request_body_template_replaces_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    template = json.dumps(
        {
            "model": "{{model}}",
            "prompt": "{{prompt}}",
            "max_tokens": 1024,
            "custom_field": "{{custom_value}}",
        }
    )
    endpoint = EndpointStub(
        id=1,
        name="TemplateEndpoint",
        base_url="https://api.example.com",
        provider="custom",
        request_body_template=template,
    )
    api_key = APIKeyStub(id=101, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o-mini")

    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"choices": [{"text": "ok"}]})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={
                "model": "gpt-4o-mini",
                "custom_value": "my-value",
                "messages": [{"role": "user", "content": "Hello world"}],
            },
        )
        assert response.status_code == 200

    await upstream_client.aclose()

    assert len(upstream_requests) == 1
    sent_body = json.loads(upstream_requests[0].content)
    assert sent_body["model"] == "gpt-4o-mini"
    assert sent_body["prompt"] == "Hello world"
    assert sent_body["max_tokens"] == 1024
    assert sent_body["custom_field"] == "my-value"


@pytest.mark.asyncio
async def test_standard_provider_ignores_request_body_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = json.dumps({"model": "{{model}}", "prompt": "{{prompt}}"})
    endpoint = EndpointStub(
        id=3,
        name="StandardTemplateIgnored",
        base_url="https://api.example.com",
        provider="openai",
        request_body_template=template,
    )
    api_key = APIKeyStub(id=103, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"choices": [{"text": "ok"}]})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={
                "model": "gpt-4o-mini",
                "custom_value": "my-value",
                "messages": [{"role": "user", "content": "Hello world"}],
            },
        )
        assert response.status_code == 200

    await upstream_client.aclose()

    assert len(upstream_requests) == 1
    sent_body = json.loads(upstream_requests[0].content)
    assert sent_body["model"] == "gpt-4o"
    assert sent_body["custom_value"] == "my-value"
    assert "prompt" not in sent_body


@pytest.mark.asyncio
async def test_request_body_template_fallback_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=2,
        name="InvalidTemplateEndpoint",
        base_url="https://api.example.com",
        provider="custom",
        request_body_template="this is not json {{model}}",
    )
    api_key = APIKeyStub(id=102, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o-mini")

    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, json={"choices": [{"text": "ok"}]})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "test"},
        )
        assert response.status_code == 200

    await upstream_client.aclose()

    assert len(upstream_requests) == 1
    sent_body = json.loads(upstream_requests[0].content)
    assert sent_body["model"] == "gpt-4o-mini"
    assert sent_body["prompt"] == "test"

import asyncio
import json

import httpx
import pytest

from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


@pytest.mark.asyncio
async def test_proxy_retries_primary_then_skips_open_circuit_on_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=10, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=11, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=12, key="sk-primary")
    fallback_key = APIKeyStub(id=13, key="sk-fallback")
    primary_candidate = RouteCandidate(
        api_key=primary_key,
        endpoint=primary_endpoint,
        real_model="gpt-4o",
    )
    fallback_candidate = RouteCandidate(
        api_key=fallback_key,
        endpoint=fallback_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "Bearer sk-primary":
            return httpx.Response(500, json={"error": "retry next candidate"})
        return httpx.Response(200, json={"id": "cmpl-retry-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [primary_candidate, fallback_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )
        second_response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi again"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-retry-ok"
    assert second_response.status_code == 200
    assert second_response.json()["id"] == "cmpl-retry-ok"
    assert len(requests) == 5
    primary_requests = [
        request for request in requests if request.headers.get("Authorization") == "Bearer sk-primary"
    ]
    fallback_requests = [
        request for request in requests if request.headers.get("Authorization") == "Bearer sk-fallback"
    ]
    assert len(primary_requests) == 3
    assert len(fallback_requests) == 2
    assert response.headers["x-api-key-id"] == str(fallback_key.id)
    assert second_response.headers["x-api-key-id"] == str(fallback_key.id)
    redis = recorded["redis"]
    assert redis.store[f"circuit:{primary_key.id}:state"] == "open"


@pytest.mark.asyncio
async def test_proxy_does_not_retry_same_candidate_on_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=14, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=15, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=16, key="sk-primary")
    fallback_key = APIKeyStub(id=17, key="sk-fallback")
    primary_candidate = RouteCandidate(
        api_key=primary_key,
        endpoint=primary_endpoint,
        real_model="gpt-4o",
    )
    fallback_candidate = RouteCandidate(
        api_key=fallback_key,
        endpoint=fallback_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "Bearer sk-primary":
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(200, json={"id": "cmpl-fallback-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [primary_candidate, fallback_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert len(requests) == 2
    assert requests[0].headers.get("Authorization") == "Bearer sk-primary"
    assert requests[1].headers.get("Authorization") == "Bearer sk-fallback"


@pytest.mark.asyncio
async def test_proxy_falls_back_on_bad_request_without_opening_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=18, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=19, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=20, key="sk-primary")
    fallback_key = APIKeyStub(id=21, key="sk-fallback")
    primary_candidate = RouteCandidate(
        api_key=primary_key,
        endpoint=primary_endpoint,
        real_model="gpt-4o",
    )
    fallback_candidate = RouteCandidate(
        api_key=fallback_key,
        endpoint=fallback_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "Bearer sk-primary":
            return httpx.Response(400, json={"error": "model_not_supported"})
        return httpx.Response(200, json={"id": "cmpl-fallback-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [primary_candidate, fallback_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-fallback-ok"
    assert len(requests) == 2
    assert requests[0].headers.get("Authorization") == "Bearer sk-primary"
    assert requests[1].headers.get("Authorization") == "Bearer sk-fallback"
    redis = recorded["redis"]
    assert f"circuit:{primary_key.id}:state" not in redis.store


@pytest.mark.asyncio
async def test_proxy_falls_back_on_semantic_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_endpoint = EndpointStub(id=32, name="Primary", base_url="https://api.example.com")
    fallback_endpoint = EndpointStub(id=33, name="Fallback", base_url="https://api.example.com")
    primary_key = APIKeyStub(id=34, key="sk-primary")
    fallback_key = APIKeyStub(id=35, key="sk-fallback")
    primary_candidate = RouteCandidate(
        api_key=primary_key,
        endpoint=primary_endpoint,
        real_model="gpt-4o",
    )
    fallback_candidate = RouteCandidate(
        api_key=fallback_key,
        endpoint=fallback_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "Bearer sk-primary":
            return httpx.Response(
                200,
                json={"error": {"message": "insufficient quota"}},
            )
        return httpx.Response(200, json={"id": "cmpl-semantic-fallback-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [primary_candidate, fallback_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json()["id"] == "cmpl-semantic-fallback-ok"
    assert len(requests) == 2
    assert requests[0].headers.get("Authorization") == "Bearer sk-primary"
    assert requests[1].headers.get("Authorization") == "Bearer sk-fallback"
    redis = recorded["redis"]
    assert redis.store[f"circuit:{primary_key.id}:failures"] == "1"
    attempts = recorded.get("attempts")
    assert attempts is not None
    assert [attempt.outcome for attempt in attempts] == ["fallback", "success"]
    assert attempts[0].failure_reason == "semantic_error_field"
    assert attempts[1].api_key_id == fallback_key.id


@pytest.mark.asyncio
async def test_proxy_does_not_fallback_on_nullable_response_error_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=36, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=37, key="sk-primary")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    requests: list[httpx.Request] = []
    upstream_payload = {
        "id": "resp-ok",
        "object": "response",
        "status": "completed",
        "error": None,
        "output": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "input": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert len(requests) == 1

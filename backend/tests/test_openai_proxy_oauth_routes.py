import json

import httpx
import pytest

from app.core.config import Settings
from app.services import endpoint_transport
from app.services.agent_transport import AgentResponse
from app.services.router import RouteCandidate
from app.services.secrets import ENCRYPTED_SECRET_PREFIX, encrypt_oauth_config
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


@pytest.mark.asyncio
async def test_agent_endpoint_routes_oauth_and_upstream_through_same_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=90,
        name="AgentOAuthEndpoint",
        base_url="https://api.example.com",
        agent_node="edge-oauth",
        oauth_config=json.dumps(
            {
                "token_url": "https://auth.example.com/oauth/token",
                "client_id": "agent-client",
                "client_secret": "agent-secret",
            }
        ),
    )
    candidate = RouteCandidate(
        api_key=APIKeyStub(id=91, key="sk-ignored"),
        endpoint=endpoint,
        real_model="gpt-4o",
    )

    class FakeAgentManager:
        def __init__(self) -> None:
            self.calls = []

        async def send_request(self, agent_name, request):  # noqa: ANN001
            self.calls.append((agent_name, request))
            if request.url == "https://auth.example.com/oauth/token":
                return AgentResponse(
                    status_code=200,
                    headers={"content-type": "application/json"},
                    body=json.dumps(
                        {"access_token": "agent-oauth-token", "expires_in": 3600}
                    ).encode("utf-8"),
                )
            assert request.headers["Authorization"] == "Bearer agent-oauth-token"
            return AgentResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=b'{"id":"agent-oauth-ok"}',
            )

    manager = FakeAgentManager()
    monkeypatch.setattr(endpoint_transport, "get_agent_manager", lambda: manager)

    def direct_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("via_agent OAuth channel must not connect directly")

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))
    recorded: dict[str, object] = {}
    app = build_proxy_app(
        monkeypatch,
        candidate,
        upstream_client,
        recorded,
        agent_manager=manager,
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
    assert [call[0] for call in manager.calls] == ["edge-oauth", "edge-oauth"]
    assert manager.calls[0][1].url == "https://auth.example.com/oauth/token"
    assert manager.calls[1][1].url == "https://api.example.com/v1/completions"


@pytest.mark.asyncio
async def test_oauth_injects_access_token_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth_config = json.dumps(
        encrypt_oauth_config(
            {
                "token_url": "https://auth.example.com/oauth/token",
                "client_id": "test-client",
                "client_secret": "test-secret",
            },
            settings=Settings(master_auth_token="token"),
        )
    )
    assert ENCRYPTED_SECRET_PREFIX in oauth_config
    endpoint = EndpointStub(
        id=100,
        name="OAuthEndpoint",
        base_url="https://api.example.com",
        oauth_config=oauth_config,
    )
    api_key = APIKeyStub(id=101, key="sk-ignored")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    token_requests: list[httpx.Request] = []
    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://auth.example.com/oauth/token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": "mock-access-token", "expires_in": 3600},
            )
        upstream_requests.append(request)
        return httpx.Response(200, json={"id": "oauth-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert len(token_requests) == 1
    assert len(upstream_requests) == 1
    token_body = token_requests[0].content.decode("utf-8")
    assert "client_secret=test-secret" in token_body
    assert ENCRYPTED_SECRET_PREFIX not in token_body
    sent_request = upstream_requests[0]
    assert sent_request.headers.get("authorization") == "Bearer mock-access-token"


@pytest.mark.asyncio
async def test_oauth_retries_on_401_with_force_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth_config = json.dumps(
        {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "retry-client",
            "client_secret": "retry-secret",
        }
    )
    endpoint = EndpointStub(
        id=110,
        name="OAuthRetryEndpoint",
        base_url="https://api.example.com",
        oauth_config=oauth_config,
    )
    api_key = APIKeyStub(id=111, key="sk-retry-ignored")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    token_requests: list[httpx.Request] = []
    upstream_requests: list[httpx.Request] = []
    call_counter = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://auth.example.com/oauth/token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": "fresh-token", "expires_in": 3600},
            )
        upstream_requests.append(request)
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"id": "oauth-retry-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert len(upstream_requests) == 2
    assert len(token_requests) == 2
    assert upstream_requests[1].headers.get("authorization") == "Bearer fresh-token"


@pytest.mark.asyncio
async def test_oauth_uses_cached_token_on_subsequent_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth_config = json.dumps(
        {
            "token_url": "https://auth.example.com/oauth/token",
            "client_id": "cache-client",
            "client_secret": "cache-secret",
        }
    )
    endpoint = EndpointStub(
        id=120,
        name="OAuthCacheEndpoint",
        base_url="https://api.example.com",
        oauth_config=oauth_config,
    )
    api_key = APIKeyStub(id=121, key="sk-cache-ignored")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    token_requests: list[httpx.Request] = []
    upstream_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == "https://auth.example.com/oauth/token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": "cached-token", "expires_in": 3600},
            )
        upstream_requests.append(request)
        return httpx.Response(200, json={"id": "oauth-cache-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response1 = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "first"},
        )
        assert response1.status_code == 200
        assert len(token_requests) == 1
        assert len(upstream_requests) == 1

        response2 = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token"},
            json={"model": "gpt-4o-mini", "prompt": "second"},
        )
        assert response2.status_code == 200

    await upstream_client.aclose()

    assert len(token_requests) == 1
    assert len(upstream_requests) == 2
    assert upstream_requests[0].headers.get("authorization") == "Bearer cached-token"
    assert upstream_requests[1].headers.get("authorization") == "Bearer cached-token"

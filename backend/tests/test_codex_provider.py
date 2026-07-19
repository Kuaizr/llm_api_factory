import json
import asyncio
import time

import httpx
import pytest

from app.core.config import Settings
from app.services.codex_oauth import parse_codex_credential, resolve_codex_credential
from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


def _mock_codex_key(**overrides: object) -> str:
    payload = {
        "access_token": "mock-access-token",
        "refresh_token": "mock-refresh-token",
        "account_id": "mock-account-id",
        "expires_at": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_codex_provider_uses_backend_api_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=201,
        name="Codex",
        base_url="https://chatgpt.example.test",
        provider="codex",
    )
    api_key = APIKeyStub(id=202, key=_mock_codex_key())
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gpt-5.5-codex-real",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "x-codex-primary-used-percent": "12.5",
                "x-codex-primary-window-minutes": "180",
                "x-codex-secondary-used-percent": "34.5",
                "x-codex-secondary-window-minutes": "10080",
            },
            json={"id": "resp-codex", "object": "response"},
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={
                "Authorization": "Bearer token",
                "User-Agent": "codex-cli/1.0",
                "Originator": "codex_cli_rs",
                "Session-Id": "sess-123",
                "Thread-Id": "thread-123",
            },
            json={
                "model": "gpt-5.5",
                "input": "hi",
                "max_output_tokens": 64,
                "temperature": 0.2,
                "stream": False,
            },
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert recorded["candidate_kwargs"]["exposure_format"] == "codex"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/backend-api/codex/responses"
    assert sent_request.headers.get("authorization") == "Bearer mock-access-token"
    assert sent_request.headers.get("chatgpt-account-id") == "mock-account-id"
    assert sent_request.headers.get("openai-beta") == "responses=experimental"
    assert sent_request.headers.get("originator") == "codex_cli_rs"
    assert sent_request.headers.get("session-id") == "sess-123"
    assert sent_request.headers.get_list("content-type") == ["application/json"]
    assert sent_request.headers.get_list("accept") == ["application/json"]
    assert sent_request.headers.get_list("openai-beta") == ["responses=experimental"]
    body = json.loads(sent_request.content.decode("utf-8"))
    assert body["model"] == "gpt-5.5-codex-real"
    assert body["instructions"] == ""
    assert body["store"] is False
    assert "max_output_tokens" not in body
    assert "temperature" not in body
    raw_usage = await recorded["redis"].get("codex:usage:202")
    assert raw_usage
    usage = json.loads(raw_usage)
    assert usage["primary"]["used_percent"] == 12.5
    assert usage["secondary"]["window_minutes"] == 10080


@pytest.mark.asyncio
async def test_codex_credential_refresh_uses_mock_token_endpoint() -> None:
    api_key = APIKeyStub(
        id=203,
        key=_mock_codex_key(access_token="", expires_at=int(time.time()) - 60),
    )

    class FakeSession:
        committed = False
        rolled_back = False

        async def commit(self) -> None:
            self.committed = True

        async def rollback(self) -> None:
            self.rolled_back = True

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "mock-new-access-token",
                "refresh_token": "mock-new-refresh-token",
                "account_id": "mock-new-account-id",
                "expires_in": 7200,
            },
        )

    settings = Settings(codex_oauth_token_url="https://auth.example.test/oauth/token")
    session = FakeSession()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    credential = await resolve_codex_credential(
        api_key,
        client=client,
        session=session,  # type: ignore[arg-type]
        settings=settings,
    )
    await client.aclose()

    assert credential.access_token == "mock-new-access-token"
    assert credential.refresh_token == "mock-new-refresh-token"
    assert credential.account_id == "mock-new-account-id"
    assert session.committed is True
    assert requests
    assert requests[0].url == "https://auth.example.test/oauth/token"
    persisted = parse_codex_credential(api_key.key)
    assert persisted.access_token == "mock-new-access-token"


@pytest.mark.asyncio
async def test_codex_credential_refreshes_when_account_id_missing() -> None:
    api_key = APIKeyStub(
        id=204,
        key=_mock_codex_key(account_id=""),
    )

    class FakeSession:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "mock-refreshed-access-token",
                "refresh_token": "mock-refreshed-refresh-token",
                "account_id": "mock-refreshed-account-id",
                "expires_in": 3600,
            },
        )

    settings = Settings(codex_oauth_token_url="https://auth.example.test/oauth/token")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    credential = await resolve_codex_credential(
        api_key,
        client=client,
        session=FakeSession(),  # type: ignore[arg-type]
        settings=settings,
    )
    await client.aclose()

    assert credential.access_token == "mock-refreshed-access-token"
    assert credential.account_id == "mock-refreshed-account-id"

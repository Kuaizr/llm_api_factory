import asyncio
import json

import httpx
import pytest

from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


@pytest.mark.asyncio
async def test_anthropic_messages_stream_preserves_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=44,
        name="Anthropic",
        base_url="https://api.anthropic.com",
        provider="anthropic",
        auth_header_name="x-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=45, key="sk-anthropic")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="claude-sonnet")
    requests: list[httpx.Request] = []
    stream_payload = b"event: content_block_delta\ndata: {\"type\":\"content_block_delta\"}\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {
        "model": "claude-alias",
        "stream": True,
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "image", "source": {"type": "base64", "data": "abc"}},
                ],
            }
        ],
        "thinking": {"type": "enabled", "budget_tokens": 1024},
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/anthropic/v1/messages",
            headers={"x-api-key": "token"},
            json=body,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.content == stream_payload
    sent_payload = json.loads(requests[0].content)
    assert sent_payload["model"] == "claude-sonnet"
    assert sent_payload["messages"] == body["messages"]
    assert sent_payload["thinking"] == body["thinking"]
    assert requests[0].headers.get("x-api-key") == "sk-anthropic"


@pytest.mark.asyncio
async def test_gemini_stream_generate_content_rewrites_model_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=46,
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="gemini",
        auth_header_name="x-goog-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=47, key="gemini-key")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gemini-2.0-flash",
    )
    requests: list[httpx.Request] = []
    stream_payload = b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/gemini/v1beta/models/gemini-alias:streamGenerateContent",
            headers={"x-goog-api-key": "token", "accept": "text/event-stream"},
            json=body,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.content == stream_payload
    assert requests[0].url.path == "/v1beta/models/gemini-2.0-flash:streamGenerateContent"
    assert json.loads(requests[0].content) == body


@pytest.mark.asyncio
async def test_proxy_stream_path_records_usage_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=40, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=41, key="sk-stream")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")
    requests: list[httpx.Request] = []

    stream_payload = (
        b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
        b'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 2, "output_tokens": 4, "total_tokens": 6}}}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer token"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )

    await upstream_client.aclose()
    for _ in range(5):
        if recorded.get("metrics") is not None:
            break
        await asyncio.sleep(0)

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.prompt_tokens == 2
    assert metrics.completion_tokens == 4
    assert metrics.total_tokens == 6
    assert metrics.ttft_ms is not None
    sent_payload = json.loads(requests[0].content)
    assert sent_payload["stream_options"]["include_usage"] is True


@pytest.mark.asyncio
async def test_gemini_stream_records_usage_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(
        id=48,
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="gemini",
        auth_header_name="x-goog-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=49, key="gemini-key")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gemini-2.0-flash",
    )
    stream_payload = (
        b'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}\n\n'
        b'data: {"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5, "totalTokenCount": 8}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream_payload,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/gemini/v1beta/models/gemini-alias:streamGenerateContent",
            headers={"x-goog-api-key": "token", "accept": "text/event-stream"},
            json=body,
        )

    await upstream_client.aclose()
    for _ in range(5):
        if recorded.get("metrics") is not None:
            break
        await asyncio.sleep(0)

    assert response.status_code == 200
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.prompt_tokens == 3
    assert metrics.completion_tokens == 5
    assert metrics.total_tokens == 8

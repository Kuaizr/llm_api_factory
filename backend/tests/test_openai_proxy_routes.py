import json
import asyncio

import httpx
import pytest

from app.services.router import RouteCandidate
from proxy_test_utils import APIKeyStub, EndpointStub, build_proxy_app


@pytest.mark.asyncio
async def test_completions_proxy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=1, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=2, key="sk-test")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {
        "id": "cmpl-1",
        "object": "text_completion",
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "hi"},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4o-mini"
    assert recorded["rule_group"] == "default"
    assert response.headers["x-real-model"] == "gpt-4o"
    assert requests
    sent_request = requests[0]
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_completions_proxy_accepts_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=7, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=8, key="sk-test-x")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"id": "cmpl-x-key", "object": "text_completion"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "prompt": "hello"},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["rule_group"] == "default"
    assert response.headers.get("x-request-id")
    assert response.headers.get("x-trace-id")
    assert "x-real-model" not in response.headers
    assert "x-api-key-id" not in response.headers
    assert requests
    sent_request = requests[0]
    assert sent_request.headers.get("authorization") == "Bearer sk-test-x"
    assert sent_request.headers.get("x-api-key") is None


@pytest.mark.asyncio
async def test_openai_standard_passthrough_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=9, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=10, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"id": "chatcmpl-openai", "object": "chat.completion"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4o-mini"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/chat/completions"
    assert sent_request.headers.get("authorization") == "Bearer sk-openai"
    assert sent_request.headers.get("x-api-key") is None
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_standard_provider_ignores_custom_endpoint_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=93,
        name="OpenAI",
        base_url="https://api.example.com",
        provider="openai",
        extra_headers=json.dumps({"X-Injected": "yes"}),
        extra_cookies="session=custom",
        extra_query_params=json.dumps({"api-version": "custom"}),
        url_path_suffix="/custom/path",
    )
    api_key = APIKeyStub(id=94, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "chatcmpl-openai"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions?existing=1",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/chat/completions"
    assert sent_request.url.query == b"existing=1"
    assert sent_request.headers.get("x-injected") is None
    assert sent_request.headers.get("cookie") is None


@pytest.mark.asyncio
async def test_custom_provider_applies_endpoint_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=95,
        name="Custom",
        base_url="https://custom.example.com",
        provider="custom",
        extra_headers=json.dumps({"X-Injected": "yes"}),
        extra_cookies="session=custom",
        extra_query_params=json.dumps({"api-version": "custom"}),
        url_path_suffix="/custom/path",
    )
    api_key = APIKeyStub(id=96, key="sk-custom")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="custom/model")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "custom-ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions?existing=1",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/custom/path"
    assert sent_request.url.query == b"existing=1&api-version=custom"
    assert sent_request.headers.get("x-injected") == "yes"
    assert sent_request.headers.get("cookie") == "session=custom"


@pytest.mark.asyncio
async def test_standard_provider_returns_raw_non_stream_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=97, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=98, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    raw_response = b'{"id":"raw",   "usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=raw_response,
        )

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.content == raw_response
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.total_tokens == 3


@pytest.mark.asyncio
async def test_openai_responses_passthrough_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=91, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=92, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4.1")

    upstream_payload = {"id": "resp-openai", "object": "response"}
    requests: list[httpx.Request] = []

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
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4.1-mini", "input": "hi"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4.1-mini"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/responses"
    assert sent_request.headers.get("authorization") == "Bearer sk-openai"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4.1"
    assert payload["input"] == "hi"


@pytest.mark.asyncio
async def test_legacy_v1_routes_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=29, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=30, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "should-not-hit"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "gpt-4o-mini", "prompt": "legacy"},
        )

    await upstream_client.aclose()

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_openai_standard_models_passthrough_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=15, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=16, key="sk-openai")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"object": "list", "data": [{"id": "gpt-4o"}]}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/openai/v1/models",
            headers={"x-api-key": "token"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "/openai/v1/models"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/models"
    assert sent_request.content == b""


@pytest.mark.asyncio
async def test_openai_standard_passthrough_accepts_custom_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_endpoint = EndpointStub(
        id=17,
        name="CustomGateway",
        base_url="https://custom.example.com",
        provider="custom",
        request_body_template=json.dumps(
            {
                "model": "{{model}}",
                "prompt": "{{prompt}}",
                "source": "custom",
            }
        ),
    )
    openai_endpoint = EndpointStub(id=18, name="OpenAI", base_url="https://api.example.com")

    custom_candidate = RouteCandidate(
        api_key=APIKeyStub(id=19, key="sk-custom"),
        endpoint=custom_endpoint,
        real_model="vendor/custom-model",
    )
    openai_candidate = RouteCandidate(
        api_key=APIKeyStub(id=20, key="sk-openai"),
        endpoint=openai_endpoint,
        real_model="gpt-4o",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "ok"})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [custom_candidate, openai_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"x-api-key": "token"},
            json={"model": "gpt-4o-mini", "prompt": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.headers.get("authorization") == "Bearer sk-custom"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "vendor/custom-model"
    assert payload["prompt"] == "hello"
    assert payload["source"] == "custom"


@pytest.mark.asyncio
async def test_anthropic_standard_passthrough_filters_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_endpoint = EndpointStub(id=11, name="OpenAI", base_url="https://api.example.com")
    anthropic_endpoint = EndpointStub(
        id=12,
        name="Anthropic",
        base_url="https://api.anthropic.com",
        provider="anthropic",
    )
    openai_candidate = RouteCandidate(
        api_key=APIKeyStub(id=13, key="sk-openai"),
        endpoint=openai_endpoint,
        real_model="gpt-4o",
    )
    anthropic_candidate = RouteCandidate(
        api_key=APIKeyStub(id=14, key="sk-anthropic"),
        endpoint=anthropic_endpoint,
        real_model="claude-3-5-sonnet",
    )

    upstream_payload = {"id": "msg_1", "type": "message"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(
        monkeypatch,
        [openai_candidate, anthropic_candidate],
        upstream_client,
        recorded,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/anthropic/v1/messages",
            headers={"x-api-key": "token"},
            json={"max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/messages"
    assert sent_request.headers.get("authorization") == "Bearer sk-anthropic"
    assert sent_request.headers.get("x-api-key") is None
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload == {"max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_anthropic_standard_passthrough_falls_back_to_openai_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=21, name="Gateway", base_url="https://api.example.com")
    api_key = APIKeyStub(id=22, key="sk-openai")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="minimax/minimax-m2.5",
    )

    upstream_payload = {"id": "msg_fallback", "type": "message"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/anthropic/v1/messages",
            headers={"x-api-key": "token"},
            json={"model": "minimax-m2.5", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "minimax-m2.5"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/messages"
    assert sent_request.headers.get("authorization") == "Bearer sk-openai"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "minimax/minimax-m2.5"


@pytest.mark.asyncio
async def test_gemini_passthrough_rewrites_model_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=31,
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="gemini",
        auth_header_name="x-goog-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=32, key="gemini-upstream-key")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gemini-1.5-pro",
    )

    upstream_payload = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {
            "promptTokenCount": 2,
            "candidatesTokenCount": 3,
            "totalTokenCount": 5,
        },
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {"contents": [{"parts": [{"text": "ping"}]}]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/gemini/v1beta/models/gemini-alias:generateContent",
            headers={"x-goog-api-key": "token", "X-Debug": "true"},
            json=body,
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert response.headers["x-real-model"] == "gemini-1.5-pro"
    assert response.headers["x-execution-mode"] == "direct"
    assert recorded["model_alias"] == "gemini-alias"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1beta/models/gemini-1.5-pro:generateContent"
    assert sent_request.headers.get("x-goog-api-key") == "gemini-upstream-key"
    assert json.loads(sent_request.content.decode("utf-8")) == body

    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.total_tokens == 5
    assert metrics.execution_mode == "direct"
    assert metrics.upstream_url.endswith("/v1beta/models/gemini-1.5-pro:generateContent")


@pytest.mark.asyncio
async def test_gemini_passthrough_accepts_query_key_and_strips_it_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=33,
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        provider="gemini",
        auth_header_name="x-goog-api-key",
        auth_header_prefix="",
    )
    api_key = APIKeyStub(id=34, key="gemini-upstream-key")
    candidate = RouteCandidate(
        api_key=api_key,
        endpoint=endpoint,
        real_model="gemini-1.5-pro",
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"candidates": []})

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/gemini/v1beta/models/gemini-alias:generateContent?key=token&alt=sse",
            json={"contents": [{"parts": [{"text": "ping"}]}]},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1beta/models/gemini-1.5-pro:generateContent"
    assert sent_request.headers.get("x-goog-api-key") == "gemini-upstream-key"
    assert sent_request.url.params.get("alt") == "sse"
    assert "key" not in sent_request.url.params


@pytest.mark.asyncio
async def test_openai_chat_preserves_tools_and_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(id=42, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=43, key="sk-tools")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4.1")
    upstream_payload = {"id": "chat-tools", "choices": [{"message": {"content": "ok"}}]}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    body = {
        "model": "gpt-alias",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "parallel_tool_calls": True,
    }
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/chat/completions",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json=body,
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    sent_payload = json.loads(requests[0].content)
    assert sent_payload["model"] == "gpt-4.1"
    assert sent_payload["tools"] == body["tools"]
    assert sent_payload["tool_choice"] == "auto"
    assert sent_payload["response_format"] == body["response_format"]
    assert sent_payload["parallel_tool_calls"] is True


@pytest.mark.asyncio
async def test_responses_proxy_passthrough_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=2, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=3, key="sk-resp")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="gpt-4o")

    upstream_payload = {"id": "resp-1", "object": "response", "output": []}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    raw_payload = {
        "model": "gpt-4o-mini",
        "input": [{"role": "user", "content": "hello"}],
        "temperature": 0.1,
        "rule_group": "qiniu",
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/responses",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json=raw_payload,
        )

    await upstream_client.aclose()
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert recorded["model_alias"] == "gpt-4o-mini"
    assert recorded["rule_group"] == "qiniu"
    assert requests
    sent_request = requests[0]
    assert sent_request.url.path == "/v1/responses"
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o"
    assert payload["input"] == raw_payload["input"]
    assert payload["temperature"] == raw_payload["temperature"]
    assert payload["rule_group"] == "qiniu"
    metrics = recorded.get("metrics")
    assert metrics is not None
    assert metrics.requested_rule_group == "qiniu"
    assert metrics.rule_group == "qiniu"


@pytest.mark.asyncio
async def test_embeddings_proxy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=3, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=4, key="sk-embed")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="text-embedding-3")

    upstream_payload = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/embeddings",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"model": "text-embedding-small", "input": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert response.headers["x-real-model"] == "text-embedding-3"
    assert recorded["model_alias"] == "text-embedding-small"
    assert requests
    sent_request = requests[0]
    payload = json.loads(sent_request.content.decode("utf-8"))
    assert payload["model"] == "text-embedding-3"


@pytest.mark.asyncio
async def test_embeddings_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = EndpointStub(id=5, name="OpenAI", base_url="https://api.example.com")
    api_key = APIKeyStub(id=6, key="sk-embed")
    candidate = RouteCandidate(api_key=api_key, endpoint=endpoint, real_model="text-embedding-fallback")

    upstream_payload = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.2]}],
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=upstream_payload)

    recorded: dict[str, object] = {}
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = build_proxy_app(monkeypatch, candidate, upstream_client, recorded)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/openai/v1/embeddings",
            headers={"Authorization": "Bearer token", "X-Debug": "true"},
            json={"input": "hello"},
        )

    await upstream_client.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert response.headers["x-real-model"] == "text-embedding-fallback"


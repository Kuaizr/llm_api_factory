from types import SimpleNamespace

from app.api.v1.route_proxy_helpers import _build_upstream_headers, _filter_response_headers


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def test_build_upstream_headers_filters_sensitive_client_headers() -> None:
    endpoint = SimpleNamespace(
        provider="openai",
        auth_header_name="Authorization",
        auth_header_prefix="Bearer",
    )
    incoming = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "openai-python/1.0",
        "OpenAI-Organization": "org_123",
        "X-Stainless-Retry-Count": "1",
        "Authorization": "Bearer client-token",
        "X-Api-Key": "client-key",
        "Cookie": "session=browser",
        "Referer": "https://console.example.com",
        "Origin": "https://console.example.com",
        "X-Forwarded-For": "10.0.0.10",
        "X-Real-IP": "10.0.0.11",
        "Host": "factory.example.com",
        "Content-Length": "123",
    }

    headers = _lower_headers(_build_upstream_headers(incoming, endpoint, "sk-upstream"))

    assert headers["authorization"] == "Bearer sk-upstream"
    assert headers["content-type"] == "application/json"
    assert headers["accept"] == "text/event-stream"
    assert headers["user-agent"] == "openai-python/1.0"
    assert headers["openai-organization"] == "org_123"
    assert headers["x-stainless-retry-count"] == "1"

    assert "x-api-key" not in headers
    assert "cookie" not in headers
    assert "referer" not in headers
    assert "origin" not in headers
    assert "x-forwarded-for" not in headers
    assert "x-real-ip" not in headers
    assert "host" not in headers
    assert "content-length" not in headers


def test_build_upstream_headers_filters_codex_headers_outside_responses() -> None:
    endpoint = SimpleNamespace(
        provider="openai",
        auth_header_name="Authorization",
        auth_header_prefix="Bearer",
    )
    incoming = {
        "Content-Type": "application/json",
        "User-Agent": "codex-cli/1.0",
        "Originator": "codex_cli_rs",
        "Session-Id": "sess-123",
        "Thread-Id": "thread-123",
        "X-Client-Request-Id": "thread-123",
        "X-Codex-Beta-Features": "feature-a",
        "X-Codex-Turn-State": "turn-state-a",
        "X-Codex-Turn-Metadata": "turn-a",
    }

    headers = _lower_headers(
        _build_upstream_headers(
            incoming,
            endpoint,
            "sk-upstream",
            request_path="/openai/v1/chat/completions",
            payload={"model": "gpt-5.5"},
        )
    )

    assert headers["authorization"] == "Bearer sk-upstream"
    assert headers["user-agent"] == "codex-cli/1.0"
    assert "originator" not in headers
    assert "session-id" not in headers
    assert "thread-id" not in headers
    assert "x-client-request-id" not in headers
    assert "x-codex-beta-features" not in headers
    assert "x-codex-turn-state" not in headers
    assert "x-codex-turn-metadata" not in headers


def test_build_upstream_headers_passes_codex_headers_for_responses() -> None:
    endpoint = SimpleNamespace(
        provider="openai",
        auth_header_name="Authorization",
        auth_header_prefix="Bearer",
    )
    incoming = {
        "Content-Type": "application/json",
        "User-Agent": "codex-cli/1.0",
        "Originator": "codex_cli_rs",
        "Session-Id": "sess-123",
        "Thread-Id": "thread-123",
        "X-Client-Request-Id": "thread-123",
        "X-Codex-Beta-Features": "feature-a",
        "X-Codex-Installation-Id": "install-123",
        "X-Codex-Turn-State": "turn-state-a",
        "X-Codex-Turn-Metadata": "turn-a",
        "X-Codex-Window-Id": "window-123",
        "Cookie": "session=browser",
    }

    headers = _lower_headers(
        _build_upstream_headers(
            incoming,
            endpoint,
            "sk-upstream",
            request_path="/openai/v1/responses",
            payload={"model": "gpt-5.5", "prompt_cache_key": "cache-123"},
        )
    )

    assert headers["authorization"] == "Bearer sk-upstream"
    assert headers["originator"] == "codex_cli_rs"
    assert headers["session-id"] == "sess-123"
    assert headers["thread-id"] == "thread-123"
    assert headers["x-client-request-id"] == "thread-123"
    assert headers["x-codex-beta-features"] == "feature-a"
    assert headers["x-codex-installation-id"] == "install-123"
    assert headers["x-codex-turn-state"] == "turn-state-a"
    assert headers["x-codex-turn-metadata"] == "turn-a"
    assert headers["x-codex-window-id"] == "window-123"
    assert "cookie" not in headers


def test_build_upstream_headers_does_not_pass_codex_headers_to_anthropic() -> None:
    endpoint = SimpleNamespace(
        provider="anthropic",
        auth_header_name="x-api-key",
        auth_header_prefix="",
    )
    incoming = {
        "Content-Type": "application/json",
        "User-Agent": "codex-cli/1.0",
        "Originator": "codex_cli_rs",
        "Session_id": "sess-123",
        "Anthropic-Beta": "context-1m-2025-08-07",
    }

    headers = _lower_headers(
        _build_upstream_headers(
            incoming,
            endpoint,
            "sk-upstream",
            request_path="/anthropic/v1/messages",
            payload={"model": "claude-opus-4-8"},
        )
    )

    assert headers["x-api-key"] == "sk-upstream"
    assert headers["anthropic-beta"] == "context-1m-2025-08-07"
    assert "originator" not in headers
    assert "session_id" not in headers


def test_filter_response_headers_preserves_codex_turn_state() -> None:
    headers = _filter_response_headers(
        {
            "Content-Type": "text/event-stream",
            "Content-Length": "123",
            "X-Codex-Turn-State": "sticky-turn-state",
            "OpenAI-Model": "gpt-5.5",
        }
    )
    lowered = _lower_headers(headers)

    assert lowered["x-codex-turn-state"] == "sticky-turn-state"
    assert lowered["openai-model"] == "gpt-5.5"
    assert "content-type" not in lowered
    assert "content-length" not in lowered

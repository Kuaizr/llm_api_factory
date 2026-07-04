from types import SimpleNamespace

from app.api.v1.route_proxy_helpers import _build_upstream_headers


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

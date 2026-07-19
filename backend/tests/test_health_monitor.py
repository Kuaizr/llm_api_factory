from dataclasses import dataclass
import json
import time

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.circuit_breaker import CircuitBreaker
from app.services import endpoint_transport
from app.services.agent_transport import AgentResponse
from app.services.health_monitor import (
    HealthMonitor,
    HealthProbeStore,
    HealthTarget,
    build_probe_url,
)
from conftest import TestMemoryRedis as MemoryRedis


@dataclass
class EndpointStub:
    id: int
    name: str
    base_url: str
    auth_header_name: str = "Authorization"
    auth_header_prefix: str = "Bearer"
    probe_interval_seconds: int | None = None
    is_active: bool = True
    provider: str = "openai"
    url_path_suffix: str | None = None
    access_mode: str = "direct"
    agent_node: str | None = None


@dataclass
class APIKeyStub:
    id: int
    key: str
    rule_group: str = "default"
    is_active: bool = True


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, message: str) -> None:
        self.messages.append(message)


def test_build_probe_url_handles_base_paths() -> None:
    assert build_probe_url("https://api.openai.com") == "https://api.openai.com/v1/models"
    assert build_probe_url("https://api.openai.com/v1") == "https://api.openai.com/v1/models"
    assert build_probe_url("https://api.openai.com/v1/") == "https://api.openai.com/v1/models"
    assert (
        build_probe_url("https://api.anthropic.com", provider="anthropic")
        == "https://api.anthropic.com/v1/messages"
    )
    assert (
        build_probe_url("https://generativelanguage.googleapis.com", provider="gemini")
        == "https://generativelanguage.googleapis.com/v1beta/models"
    )
    assert (
        build_probe_url("https://generativelanguage.googleapis.com/v1beta", provider="gemini")
        == "https://generativelanguage.googleapis.com/v1beta/models"
    )
    assert (
        build_probe_url("https://gateway.example.com/v1", url_path_suffix="/healthz")
        == "https://gateway.example.com/v1/healthz"
    )
    assert (
        build_probe_url("https://chatgpt.com", provider="codex")
        == "https://chatgpt.com/backend-api/codex/models?client_version=0.144.3"
    )


@pytest.mark.asyncio
async def test_should_probe_target_supports_disable_interval() -> None:
    endpoint = EndpointStub(
        id=101,
        name="OpenAI",
        base_url="https://api.test.com",
        probe_interval_seconds=-1,
    )
    api_key = APIKeyStub(id=102, key="sk-disabled")
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model="gpt-4o")
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    monitor = HealthMonitor(probe_store=store)

    should_probe = await monitor._should_probe_target(target, store)

    assert should_probe is False


@pytest.mark.asyncio
@respx.mock
async def test_probe_target_records_success_and_store() -> None:
    endpoint = EndpointStub(id=1, name="OpenAI", base_url="https://api.test.com")
    api_key = APIKeyStub(id=2, key="sk-test")
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model="gpt-4o")
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    settings = Settings(
        health_probe_latency_threshold_ms=0,
        circuit_breaker_failures=2,
        circuit_breaker_ttl_seconds=60,
    )
    breaker = CircuitBreaker(redis, settings=settings)

    async with httpx.AsyncClient() as client:
        monitor = HealthMonitor(
            client=client,
            circuit_breaker=breaker,
            probe_store=store,
            settings=settings,
        )
        respx.get(build_probe_url(endpoint.base_url)).mock(return_value=httpx.Response(200))

        await monitor.probe_target(target)

    result = await store.read(api_key.id)
    assert result is not None
    assert result.status == "success"
    assert result.status_code == 200
    assert result.endpoint_name == endpoint.name
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_scheduled_probe_uses_endpoint_agent_without_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = EndpointStub(
        id=31,
        name="Agent OpenAI",
        base_url="https://api.example.test",
        access_mode="via_agent",
        agent_node="edge-us",
    )
    api_key = APIKeyStub(id=32, key="sk-agent")
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model="gpt-agent")
    store = HealthProbeStore(MemoryRedis())

    class FakeAgentManager:
        def __init__(self) -> None:
            self.calls = []

        async def send_request(self, agent_name, request):  # noqa: ANN001
            self.calls.append((agent_name, request))
            return AgentResponse(status_code=200, headers={}, body=b"{}")

    manager = FakeAgentManager()
    monkeypatch.setattr(endpoint_transport, "get_agent_manager", lambda: manager)

    def direct_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("scheduled via_agent probe must not connect directly")

    client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler))
    monitor = HealthMonitor(client=client, probe_store=store)
    await monitor.probe_target(target)
    await client.aclose()

    result = await store.read(api_key.id)
    assert result is not None
    assert result.status == "success"
    assert len(manager.calls) == 1
    assert manager.calls[0][0] == "edge-us"
    assert manager.calls[0][1].url == "https://api.example.test/v1/models"


@pytest.mark.asyncio
@respx.mock
async def test_codex_probe_uses_oauth_credential_and_models_endpoint() -> None:
    endpoint = EndpointStub(
        id=21,
        name="Codex",
        base_url="https://chatgpt.example.test",
        provider="codex",
    )
    api_key = APIKeyStub(
        id=22,
        key=json.dumps(
            {
                "access_token": "codex-access",
                "account_id": "codex-account",
                "expires_at": int(time.time()) + 3600,
            }
        ),
    )
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model=None)
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    settings = Settings(codex_client_version="9.8.7")
    route = respx.get(
        "https://chatgpt.example.test/backend-api/codex/models?client_version=9.8.7"
    ).mock(return_value=httpx.Response(200, json={"models": [{"slug": "gpt-test"}]}))

    async with httpx.AsyncClient() as client:
        monitor = HealthMonitor(client=client, probe_store=store, settings=settings)
        await monitor.probe_target(target)

    result = await store.read(api_key.id)
    assert result is not None
    assert result.status == "success"
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer codex-access"
    assert request.headers["chatgpt-account-id"] == "codex-account"
    assert request.headers["user-agent"] == "codex-cli/9.8.7"


@pytest.mark.asyncio
@respx.mock
async def test_probe_target_records_failure_and_opens_circuit() -> None:
    endpoint = EndpointStub(
        id=9,
        name="Anthropic",
        base_url="https://api.example.com/v1",
        provider="anthropic",
    )
    api_key = APIKeyStub(id=3, key="sk-open")
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model="claude")
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    notifier = FakeNotifier()
    settings = Settings(
        health_probe_latency_threshold_ms=0,
        circuit_breaker_failures=1,
        circuit_breaker_ttl_seconds=120,
    )
    breaker = CircuitBreaker(redis, notifier=notifier, settings=settings)

    async with httpx.AsyncClient() as client:
        monitor = HealthMonitor(
            client=client,
            circuit_breaker=breaker,
            notifier=notifier,
            probe_store=store,
            settings=settings,
        )
        respx.post(
            build_probe_url(endpoint.base_url, provider=endpoint.provider)
        ).mock(return_value=httpx.Response(401))

        await monitor.probe_target(target)

    result = await store.read(api_key.id)
    assert result is not None
    assert result.status == "failure"
    assert result.status_code == 401
    assert notifier.messages
    assert api_key.key not in notifier.messages[0]


@pytest.mark.asyncio
@respx.mock
async def test_probe_target_failure_sends_alert() -> None:
    endpoint = EndpointStub(id=10, name="Gemini", base_url="https://api.gemini.com")
    api_key = APIKeyStub(id=4, key="sk-alert")
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model="gemini")
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    notifier = FakeNotifier()
    settings = Settings(
        health_probe_latency_threshold_ms=0,
        circuit_breaker_failures=5,
        circuit_breaker_ttl_seconds=120,
    )
    breaker = CircuitBreaker(redis, notifier=notifier, settings=settings)

    async with httpx.AsyncClient() as client:
        monitor = HealthMonitor(
            client=client,
            circuit_breaker=breaker,
            notifier=notifier,
            probe_store=store,
            settings=settings,
        )
        respx.get(build_probe_url(endpoint.base_url)).mock(return_value=httpx.Response(503))

        await monitor.probe_target(target)

    assert any("Health probe failure alert" in message for message in notifier.messages)


@pytest.mark.asyncio
@respx.mock
async def test_probe_target_error_sends_alert() -> None:
    endpoint = EndpointStub(id=11, name="DeepSeek", base_url="https://api.deepseek.com")
    api_key = APIKeyStub(id=5, key="sk-error")
    target = HealthTarget(endpoint=endpoint, api_key=api_key, real_model="deepseek")
    redis = MemoryRedis()
    store = HealthProbeStore(redis)
    notifier = FakeNotifier()
    settings = Settings(
        health_probe_latency_threshold_ms=0,
        circuit_breaker_failures=5,
        circuit_breaker_ttl_seconds=120,
    )
    breaker = CircuitBreaker(redis, notifier=notifier, settings=settings)

    async with httpx.AsyncClient() as client:
        monitor = HealthMonitor(
            client=client,
            circuit_breaker=breaker,
            notifier=notifier,
            probe_store=store,
            settings=settings,
        )
        respx.get(build_probe_url(endpoint.base_url)).mock(
            side_effect=httpx.ConnectError("boom")
        )

        await monitor.probe_target(target)

    assert any("Health probe error alert" in message for message in notifier.messages)

from dataclasses import dataclass

import httpx
import pytest
import respx

from app.core.config import Settings
from app.services.circuit_breaker import CircuitBreaker
from app.services.health_monitor import (
    HealthMonitor,
    HealthProbeStore,
    HealthTarget,
    build_probe_url,
)


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


@dataclass
class APIKeyStub:
    id: int
    key: str
    rule_group: str = "default"
    is_active: bool = True


class MemoryRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(key) for key in keys]

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)

    async def delete(self, key: str) -> bool:
        self.store.pop(key, None)
        self.lists.pop(key, None)
        self.expirations.pop(key, None)
        return True

    async def lpush(self, key: str, value: str) -> int:
        items = self.lists.setdefault(key, [])
        items.insert(0, value)
        return len(items)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        items = self.lists.get(key, [])
        if end < 0:
            end = len(items) + end
        self.lists[key] = items[start : end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end < 0:
            end = len(items) + end
        return items[start : end + 1]


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
        build_probe_url("https://gateway.example.com/v1", url_path_suffix="/healthz")
        == "https://gateway.example.com/v1/healthz"
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

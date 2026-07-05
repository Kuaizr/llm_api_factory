import asyncio
import json
import logging
import sys

import httpx
import pytest

from app.core.config import Settings
from app.services import agent_client as agent_client_module


def test_build_heartbeat_url_rewrites_ws_endpoint() -> None:
    url = agent_client_module._build_heartbeat_url(
        "ws://localhost:8000/api/v1/agent/ws", None
    )
    assert url == "http://localhost:8000/api/v1/agent/heartbeat"


def test_build_heartbeat_url_handles_trailing_slash() -> None:
    url = agent_client_module._build_heartbeat_url(
        "ws://edge.local/agent/ws/", None
    )
    assert url == "http://edge.local/agent/heartbeat"


def test_build_heartbeat_url_preserves_query() -> None:
    url = agent_client_module._build_heartbeat_url(
        "wss://edge.local/agent/ws?token=1", None
    )
    assert url == "https://edge.local/agent/heartbeat?token=1"


def test_build_heartbeat_url_override() -> None:
    url = agent_client_module._build_heartbeat_url(
        "ws://localhost:8000/agent/ws", "http://override"
    )
    assert url == "http://override"


def test_build_agent_from_settings_uses_config(monkeypatch) -> None:
    settings = Settings(
        agent_ws_url="ws://localhost:8000/api/v1/agent/ws",
        agent_heartbeat_url="http://localhost:8000/api/v1/agent/heartbeat",
        agent_name="edge-hk",
        agent_region="hk",
        agent_network_group="egress-asia",
        agent_labels="hk,fast",
        agent_endpoint_url="https://api.example.com",
        agent_auth_token="token",
    )
    monkeypatch.setattr(agent_client_module, "get_settings", lambda: settings)

    agent = agent_client_module.build_agent_from_settings()

    assert agent is not None
    assert agent.ws_url == settings.agent_ws_url
    assert agent.heartbeat_url == settings.agent_heartbeat_url
    assert agent.name == settings.agent_name
    assert agent.region == settings.agent_region
    assert agent.network_group == settings.agent_network_group
    assert agent.labels == ["hk", "fast"]
    assert agent.endpoint_url == settings.agent_endpoint_url
    assert agent.auth_token == settings.agent_auth_token


class FakeAgent:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@pytest.mark.asyncio
async def test_run_agent_with_shutdown_cancels_task() -> None:
    agent = FakeAgent()
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        agent_client_module.run_agent_with_shutdown(agent, stop_event)
    )

    await agent.started.wait()
    stop_event.set()
    await task

    assert agent.cancelled.is_set()


class ReturningAgent:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self) -> None:
        self.started.set()


@pytest.mark.asyncio
async def test_run_agent_with_shutdown_returns_when_agent_stops() -> None:
    agent = ReturningAgent()

    await agent_client_module.run_agent_with_shutdown(agent, asyncio.Event())

    assert agent.started.is_set()


class FailingStatusClient:
    def __init__(self) -> None:
        self.called = asyncio.Event()

    async def post(self, *_args, **_kwargs) -> None:
        self.called.set()
        raise httpx.ConnectError("heartbeat unreachable")


@pytest.mark.asyncio
async def test_status_heartbeat_logs_http_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent = agent_client_module.AgentClient(
        ws_url="ws://localhost:8000/api/v1/agent/ws",
        name="edge-hk",
        auth_token="token",
        heartbeat_interval_seconds=60,
    )
    client = FailingStatusClient()

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(
            agent._status_heartbeat(
                client,
                "http://localhost:8000/api/v1/agent/heartbeat",
                {"Authorization": "Bearer token"},
            )
        )
        await asyncio.wait_for(client.called.wait(), timeout=1)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert "Agent status heartbeat failed: heartbeat unreachable" in caplog.text


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self._request_sent = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._request_sent:
            await asyncio.Event().wait()
        self._request_sent = True
        return json.dumps(
            {
                "type": "proxy_request",
                "request_id": "req-cleanup",
                "method": "POST",
                "url": "https://api.example.com/v1/chat/completions",
                "headers": {},
                "body": "",
                "stream": False,
            }
        )


class FakeWebSocketContext:
    def __init__(self) -> None:
        self.websocket = FakeWebSocket()

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *_args) -> None:
        return None


class FakeWebsocketsModule:
    def connect(self, *_args, **_kwargs) -> FakeWebSocketContext:
        return FakeWebSocketContext()


class CleanupTrackingAgent(agent_client_module.AgentClient):
    def __init__(self) -> None:
        super().__init__(
            ws_url="ws://factory.example.com/agent/ws",
            heartbeat_url="http://factory.example.com/agent/heartbeat",
            name="edge-hk",
            auth_token="token",
            heartbeat_interval_seconds=60,
            reconnect_delay_seconds=0.25,
        )
        self.heartbeat_started = asyncio.Event()
        self.heartbeat_cancelled = asyncio.Event()
        self.status_started = asyncio.Event()
        self.status_cancelled = asyncio.Event()

    async def _refresh_capabilities(self) -> None:
        return None

    async def _heartbeat(self, ws) -> None:  # noqa: ANN001
        self.heartbeat_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.heartbeat_cancelled.set()
            raise

    async def _status_heartbeat(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
    ) -> None:
        _ = (client, url, headers)
        self.status_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.status_cancelled.set()
            raise


@pytest.mark.asyncio
async def test_run_cleans_heartbeat_tasks_after_proxy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sleep = asyncio.sleep

    async def fail_proxy_request(*_args, **_kwargs) -> None:
        await original_sleep(0)
        raise RuntimeError("proxy failed")

    class StopAgent(Exception):
        pass

    async def stop_after_cleanup(delay: float) -> None:
        if delay == 0.25:
            raise StopAgent()
        await original_sleep(delay)

    monkeypatch.setitem(sys.modules, "websockets", FakeWebsocketsModule())
    monkeypatch.setattr(agent_client_module, "handle_proxy_request", fail_proxy_request)
    monkeypatch.setattr(agent_client_module.asyncio, "sleep", stop_after_cleanup)

    agent = CleanupTrackingAgent()
    task = asyncio.create_task(agent.run())
    try:
        await asyncio.wait_for(agent.heartbeat_started.wait(), timeout=1)
        await asyncio.wait_for(agent.status_started.wait(), timeout=1)
        await asyncio.wait_for(agent.heartbeat_cancelled.wait(), timeout=1)
        await asyncio.wait_for(agent.status_cancelled.wait(), timeout=1)
        with pytest.raises(StopAgent):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

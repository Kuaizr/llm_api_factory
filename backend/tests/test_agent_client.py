import asyncio

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

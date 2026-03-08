from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import signal
import time
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.core.config import get_settings
from app.services.agent_worker import handle_proxy_request


def _build_heartbeat_url(ws_url: str, override: str | None) -> str | None:
    if override:
        return override
    if not ws_url:
        return None
    parsed = urlparse(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/agent/ws"):
        path = f"{path[:-len('/agent/ws')]}/agent/heartbeat"
    else:
        path = f"{path}/agent/heartbeat" if path else "/agent/heartbeat"
    return urlunparse(parsed._replace(scheme=scheme, path=path))


OPENAI_PROBE_URL = "https://api.openai.com/v1/models"
GEMINI_PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
CLAUDE_PROBE_URL = "https://api.anthropic.com/v1/messages"


@dataclass(frozen=True)
class AgentCapabilities:
    supports_gpt: bool
    supports_gemini: bool
    supports_claude: bool
    probe_latency_ms: int | None


async def _probe_endpoint(
    client: httpx.AsyncClient, url: str, timeout_seconds: float = 5.0
) -> tuple[bool, int | None]:
    start = time.perf_counter()
    try:
        response = await client.get(url, timeout=timeout_seconds)
        response.raise_for_status()
    except httpx.HTTPStatusError:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return True, latency_ms
    except httpx.HTTPError:
        return False, None
    latency_ms = int((time.perf_counter() - start) * 1000)
    return True, latency_ms


async def probe_agent_capabilities(client: httpx.AsyncClient) -> AgentCapabilities:
    gpt_supported, gpt_latency = await _probe_endpoint(client, OPENAI_PROBE_URL)
    gemini_supported, gemini_latency = await _probe_endpoint(client, GEMINI_PROBE_URL)
    claude_supported, claude_latency = await _probe_endpoint(client, CLAUDE_PROBE_URL)
    latencies = [
        value
        for value in (gpt_latency, gemini_latency, claude_latency)
        if value is not None
    ]
    probe_latency_ms = int(sum(latencies) / len(latencies)) if latencies else None
    return AgentCapabilities(
        supports_gpt=gpt_supported,
        supports_gemini=gemini_supported,
        supports_claude=claude_supported,
        probe_latency_ms=probe_latency_ms,
    )


class AgentClient:
    def __init__(
        self,
        ws_url: str,
        name: str,
        auth_token: str | None,
        region: str | None = None,
        endpoint_url: str | None = None,
        heartbeat_url: str | None = None,
        heartbeat_interval_seconds: int = 20,
        reconnect_delay_seconds: int = 5,
    ) -> None:
        self.ws_url = ws_url
        self.name = name
        self.auth_token = auth_token
        self.region = region
        self.endpoint_url = endpoint_url
        self.heartbeat_url = heartbeat_url
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self.supports_gpt: bool | None = None
        self.supports_gemini: bool | None = None
        self.supports_claude: bool | None = None
        self.probe_latency_ms: int | None = None

    async def _refresh_capabilities(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                capabilities = await probe_agent_capabilities(client)
        except httpx.HTTPError:
            return
        self.supports_gpt = capabilities.supports_gpt
        self.supports_gemini = capabilities.supports_gemini
        self.supports_claude = capabilities.supports_claude
        self.probe_latency_ms = capabilities.probe_latency_ms

    async def run(self) -> None:
        headers = []
        auth_headers: dict[str, str] = {}
        if self.auth_token:
            headers.append(("Authorization", f"Bearer {self.auth_token}"))
            auth_headers["Authorization"] = f"Bearer {self.auth_token}"
        heartbeat_url = _build_heartbeat_url(self.ws_url, self.heartbeat_url)

        while True:
            try:
                await self._refresh_capabilities()
                import websockets

                async with websockets.connect(self.ws_url, extra_headers=headers) as ws:
                    register_payload = {
                        "type": "register",
                        "name": self.name,
                        "region": self.region,
                        "endpoint_url": self.endpoint_url,
                        "token": self.auth_token,
                        "supports_gpt": self.supports_gpt,
                        "supports_gemini": self.supports_gemini,
                        "supports_claude": self.supports_claude,
                        "probe_latency_ms": self.probe_latency_ms,
                    }
                    await ws.send(json.dumps(register_payload))
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    status_task: asyncio.Task | None = None
                    async with httpx.AsyncClient() as client:
                        if heartbeat_url:
                            status_task = asyncio.create_task(
                                self._status_heartbeat(client, heartbeat_url, auth_headers)
                            )
                        async for raw_message in ws:
                            if not raw_message:
                                continue
                            try:
                                payload = json.loads(raw_message)
                            except json.JSONDecodeError:
                                continue
                            if payload.get("type") != "proxy_request":
                                continue

                            async def send_json(message: dict[str, Any]) -> None:
                                await ws.send(json.dumps(message))

                            await handle_proxy_request(payload, client, send_json)
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                    if status_task:
                        status_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await status_task
            except Exception:
                await asyncio.sleep(self.reconnect_delay_seconds)

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            await ws.send(json.dumps({"type": "heartbeat"}))

    async def _status_heartbeat(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
    ) -> None:
        payload = {
            "name": self.name,
            "region": self.region,
            "endpoint_url": self.endpoint_url,
            "token": self.auth_token,
            "supports_gpt": self.supports_gpt,
            "supports_gemini": self.supports_gemini,
            "supports_claude": self.supports_claude,
            "probe_latency_ms": self.probe_latency_ms,
        }
        while True:
            try:
                await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError:
                pass
            await asyncio.sleep(self.heartbeat_interval_seconds)


def build_agent_from_settings() -> AgentClient | None:
    settings = get_settings()
    if not settings.agent_ws_url or not settings.agent_name:
        return None
    return AgentClient(
        ws_url=settings.agent_ws_url,
        name=settings.agent_name,
        auth_token=settings.agent_auth_token,
        region=settings.agent_region,
        endpoint_url=settings.agent_endpoint_url,
        heartbeat_url=settings.agent_heartbeat_url,
        heartbeat_interval_seconds=settings.agent_heartbeat_interval_seconds,
        reconnect_delay_seconds=settings.agent_reconnect_delay_seconds,
    )


async def run_agent_with_shutdown(
    agent: AgentClient, stop_event: asyncio.Event | None = None
) -> None:
    event = stop_event or asyncio.Event()
    task = asyncio.create_task(agent.run())
    try:
        await event.wait()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def run_agent() -> None:
    agent = build_agent_from_settings()
    if agent is None:
        raise RuntimeError("Agent config missing")
    stop_event = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)
        await run_agent_with_shutdown(agent, stop_event)

    asyncio.run(runner())


if __name__ == "__main__":
    run_agent()

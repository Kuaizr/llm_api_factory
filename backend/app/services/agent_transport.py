from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AgentRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    stream: bool


@dataclass(frozen=True)
class AgentResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


@dataclass
class AgentStream:
    request_id: str
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    _queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    _started: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait_started(self) -> None:
        await self._started.wait()

    async def iter_bytes(self):
        await self.wait_started()
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk

    async def read_all(self) -> bytes:
        chunks = []
        async for chunk in self.iter_bytes():
            chunks.append(chunk)
        return b"".join(chunks)


@dataclass
class AgentConnection:
    name: str
    channel: Any
    last_seen_at: datetime
    pending: dict[str, asyncio.Future | AgentStream] = field(default_factory=dict)

    async def send(self, payload: dict[str, Any]) -> None:
        await self.channel.send_json(payload)

    def touch(self, now: datetime | None = None) -> None:
        self.last_seen_at = now or datetime.now(timezone.utc)


class AgentUnavailableError(RuntimeError):
    pass


class AgentManager:
    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}

    def register(self, name: str, channel: Any) -> AgentConnection:
        connection = AgentConnection(name=name, channel=channel, last_seen_at=datetime.now(timezone.utc))
        self._connections[name] = connection
        return connection

    def unregister(self, name: str) -> None:
        self._connections.pop(name, None)

    def get(self, name: str) -> AgentConnection | None:
        return self._connections.get(name)

    async def send_request(self, agent_name: str, request: AgentRequest) -> AgentResponse | AgentStream:
        connection = self._connections.get(agent_name)
        if not connection:
            raise AgentUnavailableError(f"Agent {agent_name} unavailable")

        request_id = uuid4().hex
        payload = {
            "type": "proxy_request",
            "request_id": request_id,
            "method": request.method,
            "url": request.url,
            "headers": request.headers,
            "body": base64.b64encode(request.body).decode("utf-8"),
            "stream": request.stream,
        }

        if request.stream:
            stream = AgentStream(request_id=request_id)
            connection.pending[request_id] = stream
            await connection.send(payload)
            await stream.wait_started()
            return stream

        loop = asyncio.get_running_loop()
        future: asyncio.Future[AgentResponse] = loop.create_future()
        connection.pending[request_id] = future
        await connection.send(payload)
        return await future

    async def handle_message(self, agent_name: str, message: dict[str, Any]) -> None:
        connection = self._connections.get(agent_name)
        if not connection:
            return
        connection.touch()

        message_type = message.get("type")
        request_id = message.get("request_id")
        if not request_id:
            return
        pending = connection.pending.get(request_id)
        if not pending:
            return

        if message_type == "proxy_response":
            status_code = int(message.get("status_code") or 500)
            headers = message.get("headers") or {}
            if isinstance(pending, AgentStream):
                pending.status_code = status_code
                pending.headers = headers
                pending._started.set()
                return

            body_b64 = message.get("body") or ""
            try:
                body = base64.b64decode(body_b64)
            except Exception:
                body = b""
            response = AgentResponse(status_code=status_code, headers=headers, body=body)
            if not pending.done():
                pending.set_result(response)
            connection.pending.pop(request_id, None)
            return

        if isinstance(pending, AgentStream):
            if message_type == "proxy_stream":
                data_b64 = message.get("data") or ""
                try:
                    data = base64.b64decode(data_b64)
                except Exception:
                    data = b""
                await pending._queue.put(data)
                return
            if message_type in {"proxy_stream_end", "proxy_stream_error"}:
                await pending._queue.put(None)
                connection.pending.pop(request_id, None)
                return


_manager: AgentManager | None = None


def get_agent_manager() -> AgentManager:
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager

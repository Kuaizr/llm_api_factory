import asyncio
import base64

import pytest

from app.services.agent_transport import (
    AgentManager,
    AgentRequest,
    AgentResponse,
    AgentUnavailableError,
)


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


@pytest.mark.asyncio
async def test_agent_manager_send_request_returns_response() -> None:
    manager = AgentManager()
    channel = FakeChannel()
    manager.register("edge-hk", channel)

    task = asyncio.create_task(
        manager.send_request(
            "edge-hk",
            AgentRequest(
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={"x-test": "1"},
                body=b"{}",
                stream=False,
            ),
        )
    )

    await asyncio.sleep(0)
    assert channel.sent
    request_id = channel.sent[0]["request_id"]

    payload = {
        "type": "proxy_response",
        "request_id": request_id,
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": base64.b64encode(b"ok").decode("utf-8"),
    }
    await manager.handle_message("edge-hk", payload)

    response = await task
    assert response.status_code == 200
    assert response.body == b"ok"


@pytest.mark.asyncio
async def test_agent_manager_stream_request() -> None:
    manager = AgentManager()
    channel = FakeChannel()
    manager.register("edge-hk", channel)

    task = asyncio.create_task(
        manager.send_request(
            "edge-hk",
            AgentRequest(
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={},
                body=b"{}",
                stream=True,
            ),
        )
    )

    await asyncio.sleep(0)
    request_id = channel.sent[-1]["request_id"]
    await manager.handle_message(
        "edge-hk",
        {
            "type": "proxy_response",
            "request_id": request_id,
            "status_code": 200,
            "headers": {"content-type": "text/event-stream"},
        },
    )
    stream = await task
    await manager.handle_message(
        "edge-hk",
        {
            "type": "proxy_stream",
            "request_id": request_id,
            "data": base64.b64encode(b"hello").decode("utf-8"),
        },
    )
    await manager.handle_message(
        "edge-hk",
        {
            "type": "proxy_stream_end",
            "request_id": request_id,
        },
    )

    chunks = [chunk async for chunk in stream.iter_bytes()]
    assert b"".join(chunks) == b"hello"


@pytest.mark.asyncio
async def test_agent_manager_send_request_times_out_and_cleans_pending() -> None:
    manager = AgentManager(request_timeout_seconds=0.001)
    channel = FakeChannel()
    connection = manager.register("edge-hk", channel)

    with pytest.raises(AgentUnavailableError):
        await manager.send_request(
            "edge-hk",
            AgentRequest(
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={},
                body=b"{}",
                stream=False,
            ),
        )

    assert channel.sent
    assert connection.pending == {}


@pytest.mark.asyncio
async def test_agent_manager_stream_request_times_out_and_cleans_pending() -> None:
    manager = AgentManager(request_timeout_seconds=0.001)
    channel = FakeChannel()
    connection = manager.register("edge-hk", channel)

    with pytest.raises(AgentUnavailableError):
        await manager.send_request(
            "edge-hk",
            AgentRequest(
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={},
                body=b"{}",
                stream=True,
            ),
        )

    assert channel.sent
    assert connection.pending == {}


@pytest.mark.asyncio
async def test_agent_manager_stream_idle_timeout_cleans_pending() -> None:
    manager = AgentManager(stream_idle_timeout_seconds=0.001)
    channel = FakeChannel()
    connection = manager.register("edge-hk", channel)

    task = asyncio.create_task(
        manager.send_request(
            "edge-hk",
            AgentRequest(
                method="POST",
                url="https://api.example.com/v1/chat/completions",
                headers={},
                body=b"{}",
                stream=True,
            ),
        )
    )

    await asyncio.sleep(0)
    request_id = channel.sent[-1]["request_id"]
    await manager.handle_message(
        "edge-hk",
        {
            "type": "proxy_response",
            "request_id": request_id,
            "status_code": 200,
            "headers": {"content-type": "text/event-stream"},
        },
    )
    stream = await task
    assert request_id in connection.pending

    with pytest.raises(AgentUnavailableError):
        _ = [chunk async for chunk in stream.iter_bytes()]

    assert connection.pending == {}


@pytest.mark.asyncio
async def test_agent_manager_shutdown_sends_shutdown_and_unregisters() -> None:
    manager = AgentManager()
    channel = FakeChannel()
    connection = manager.register("edge-hk", channel)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[AgentResponse] = loop.create_future()
    connection.pending["req-1"] = future

    assert await manager.shutdown("edge-hk") is True

    assert channel.sent[-1] == {"type": "shutdown", "reason": "deleted"}
    assert channel.closed is True
    assert future.cancelled()
    assert connection.pending == {}
    assert manager.get("edge-hk") is None

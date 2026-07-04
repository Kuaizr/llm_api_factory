import json

import httpx
import pytest
import respx

from app.services.telegram import TelegramNotifier


@pytest.mark.asyncio
@respx.mock
async def test_send_message_when_enabled() -> None:
    async with httpx.AsyncClient() as client:
        notifier = TelegramNotifier(
            token="test-token", chat_id="123", enabled=True, client=client
        )
        route = respx.post(
            "https://api.telegram.org/bottest-token/sendMessage"
        ).mock(return_value=httpx.Response(200, json={"ok": True}))

        await notifier.send_message("hello")

        assert route.called
        request = route.calls[0].request
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["chat_id"] == "123"
        assert payload["text"] == "hello"


@pytest.mark.asyncio
@respx.mock
async def test_send_message_skipped_when_disabled() -> None:
    async with httpx.AsyncClient() as client:
        notifier = TelegramNotifier(
            token="test-token", chat_id="123", enabled=False, client=client
        )
        route = respx.post(
            "https://api.telegram.org/bottest-token/sendMessage"
        ).mock(return_value=httpx.Response(200, json={"ok": True}))

        await notifier.send_message("hello")

        assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_send_message_retries_transient_server_errors() -> None:
    async with httpx.AsyncClient() as client:
        notifier = TelegramNotifier(
            token="test-token",
            chat_id="123",
            enabled=True,
            client=client,
            retry_delay_seconds=0,
        )
        route = respx.post(
            "https://api.telegram.org/bottest-token/sendMessage"
        ).mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})])

        await notifier.send_message("hello")

        assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_send_message_does_not_retry_client_errors(caplog: pytest.LogCaptureFixture) -> None:
    async with httpx.AsyncClient() as client:
        notifier = TelegramNotifier(
            token="test-token",
            chat_id="123",
            enabled=True,
            client=client,
            retry_delay_seconds=0,
        )
        route = respx.post(
            "https://api.telegram.org/bottest-token/sendMessage"
        ).mock(return_value=httpx.Response(400, json={"ok": False}))

        await notifier.send_message("hello")

        assert route.call_count == 1
        assert "Telegram alert rejected with status 400" in caplog.text

import asyncio
import logging
from typing import Any

from httpx import AsyncClient, HTTPError

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(
        self,
        token: str | None,
        chat_id: str | None,
        enabled: bool = False,
        client: AsyncClient | None = None,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._enabled = enabled and bool(token and chat_id)
        self._client = client
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_delay_seconds = max(0.0, float(retry_delay_seconds))

    async def _post(self, url: str, payload: dict[str, Any]):
        if self._client is not None:
            return await self._client.post(url, json=payload)
        async with AsyncClient() as client:
            return await client.post(url, json=payload)

    async def send_message(self, text: str) -> None:
        if not self._enabled or not self._token or not self._chat_id:
            return

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": self._chat_id, "text": text}

        for attempt in range(self._retry_attempts):
            try:
                response = await self._post(url, payload)
                if response.status_code < 400:
                    return
                if response.status_code < 500:
                    logger.warning(
                        "Telegram alert rejected with status %s",
                        response.status_code,
                    )
                    return
                if attempt + 1 >= self._retry_attempts:
                    logger.warning(
                        "Telegram alert failed after %s attempts with status %s",
                        self._retry_attempts,
                        response.status_code,
                    )
                    return
            except HTTPError as exc:
                if attempt + 1 >= self._retry_attempts:
                    logger.warning(
                        "Telegram alert failed after %s attempts: %s",
                        self._retry_attempts,
                        exc,
                    )
                    return
            if self._retry_delay_seconds:
                await asyncio.sleep(self._retry_delay_seconds)

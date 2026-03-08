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
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._enabled = enabled and bool(token and chat_id)
        self._client = client

    async def send_message(self, text: str) -> None:
        if not self._enabled or not self._token or not self._chat_id:
            return

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": self._chat_id, "text": text}

        try:
            if self._client is not None:
                await self._client.post(url, json=payload)
            else:
                async with AsyncClient() as client:
                    await client.post(url, json=payload)
        except HTTPError as exc:
            logger.warning("Telegram alert failed: %s", exc)

from __future__ import annotations

import base64
from typing import Any, Awaitable, Callable

from httpx import AsyncClient, HTTPError


SendFunc = Callable[[dict[str, Any]], Awaitable[None]]


def _decode_body(encoded: str | None) -> bytes:
    if not encoded:
        return b""
    try:
        return base64.b64decode(encoded)
    except Exception:
        return b""


def _encode_body(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


async def handle_proxy_request(
    payload: dict[str, Any],
    client: AsyncClient,
    send: SendFunc,
) -> None:
    request_id = payload.get("request_id")
    if not request_id:
        return

    method = str(payload.get("method") or "POST")
    url = payload.get("url")
    if not url:
        await send(
            {
                "type": "proxy_response",
                "request_id": request_id,
                "status_code": 400,
                "headers": {},
                "body": _encode_body(b"missing url"),
            }
        )
        return

    headers = payload.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    body = _decode_body(payload.get("body"))
    stream = bool(payload.get("stream"))

    try:
        if stream:
            request_obj = client.build_request(
                method, url, headers=headers, content=body
            )
            response = await client.send(request_obj, stream=True)
            await send(
                {
                    "type": "proxy_response",
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                }
            )
            async for chunk in response.aiter_bytes():
                if chunk:
                    await send(
                        {
                            "type": "proxy_stream",
                            "request_id": request_id,
                            "data": _encode_body(chunk),
                        }
                    )
            await send({"type": "proxy_stream_end", "request_id": request_id})
            await response.aclose()
            return

        response = await client.request(method, url, headers=headers, content=body)
        content = await response.aread()
        await response.aclose()
        await send(
            {
                "type": "proxy_response",
                "request_id": request_id,
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": _encode_body(content),
            }
        )
    except HTTPError:
        error_payload = {
            "type": "proxy_response",
            "request_id": request_id,
            "status_code": 502,
            "headers": {},
            "body": _encode_body(b"upstream_error"),
        }
        await send(error_payload)
        if stream:
            await send({"type": "proxy_stream_error", "request_id": request_id})

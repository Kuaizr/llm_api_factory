from __future__ import annotations

import base64
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from httpx import AsyncClient, HTTPError

from app.core.config import get_settings


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


def _target_entries(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _split_host_port(entry: str) -> tuple[str, int | None]:
    if entry.startswith("["):
        host, _, rest = entry[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return host.lower(), int(rest[1:])
        return host.lower(), None
    if entry.count(":") == 1:
        host, port = entry.rsplit(":", 1)
        if port.isdigit():
            return host.lower(), int(port)
    return entry.lower(), None


def _entry_matches(host: str, port: int | None, entry: str) -> bool:
    if entry == "*":
        return True

    entry_host, entry_port = _split_host_port(entry)
    if entry_port is not None and port != entry_port:
        return False

    try:
        network = ip_network(entry_host, strict=False)
        target_ip = ip_address(host)
    except ValueError:
        network = None
        target_ip = None
    if network is not None and target_ip is not None:
        return target_ip in network

    normalized_host = host.lower().strip("[]")
    if entry_host.startswith("*."):
        suffix = entry_host[1:]
        return normalized_host.endswith(suffix)
    return normalized_host == entry_host


def _parse_ip_literal(host: str) -> IPv4Address | IPv6Address | None:
    try:
        return ip_address(host.strip("[]"))
    except ValueError:
        return None


def _is_restricted_literal_target(host: str) -> bool:
    target_ip = _parse_ip_literal(host)
    if target_ip is None:
        normalized = host.lower().strip("[]")
        return normalized == "localhost" or normalized.endswith(".localhost")
    return not target_ip.is_global


def _is_target_allowed(url: str, allowed_targets: str | None) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False

    host = parsed.hostname.lower()
    port = parsed.port
    entries = _target_entries(allowed_targets)
    if _is_restricted_literal_target(host):
        return any(entry != "*" and _entry_matches(host, port, entry) for entry in entries)

    for entry in entries:
        if _entry_matches(host, port, entry):
            return True
    return False


async def _send_error(
    send: SendFunc,
    request_id: str,
    status_code: int,
    message: bytes,
) -> None:
    await send(
        {
            "type": "proxy_response",
            "request_id": request_id,
            "status_code": status_code,
            "headers": {},
            "body": _encode_body(message),
        }
    )


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
        await _send_error(send, request_id, 400, b"missing url")
        return
    url = str(url)
    settings = get_settings()
    if not _is_target_allowed(url, settings.agent_allowed_targets):
        await _send_error(send, request_id, 403, b"target_not_allowed")
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

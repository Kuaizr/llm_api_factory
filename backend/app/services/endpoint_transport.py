from __future__ import annotations

from dataclasses import dataclass

from app.services.agent_transport import (
    AgentRequest,
    AgentResponse,
    AgentUnavailableError,
    get_agent_manager,
)


@dataclass(frozen=True)
class EndpointTransportResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


def endpoint_agent_name(endpoint: object | None) -> str | None:
    if endpoint is None:
        return None
    access_mode = str(getattr(endpoint, "access_mode", "") or "").strip()
    if access_mode and access_mode != "via_agent":
        return None
    agent_name = str(getattr(endpoint, "agent_node", "") or "").strip()
    if access_mode == "via_agent" and not agent_name:
        raise AgentUnavailableError("Endpoint requires an Agent but agent_node is missing")
    return agent_name or None


async def send_endpoint_request(
    *,
    endpoint: object | None,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    client: object,
    timeout: float | None = None,
) -> EndpointTransportResponse:
    normalized_method = method.upper()
    agent_name = endpoint_agent_name(endpoint)
    if agent_name:
        response = await get_agent_manager().send_request(
            agent_name,
            AgentRequest(
                method=normalized_method,
                url=url,
                headers=dict(headers),
                body=body,
                stream=False,
            ),
        )
        if not isinstance(response, AgentResponse):
            raise AgentUnavailableError(
                f"Agent {agent_name} returned an unexpected stream response"
            )
        return EndpointTransportResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.body,
        )

    request = getattr(client, "request", None)
    request_kwargs: dict[str, object] = {
        "headers": headers,
        "content": body,
    }
    if timeout is not None:
        request_kwargs["timeout"] = timeout
    if callable(request):
        response = await request(
            normalized_method,
            url,
            **request_kwargs,
        )
    elif normalized_method == "POST":
        response = await client.post(url, **request_kwargs)
    else:
        get_kwargs: dict[str, object] = {"headers": headers}
        if timeout is not None:
            get_kwargs["timeout"] = timeout
        response = await client.get(url, **get_kwargs)
    try:
        content = await response.aread()
        return EndpointTransportResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=content,
        )
    finally:
        await response.aclose()

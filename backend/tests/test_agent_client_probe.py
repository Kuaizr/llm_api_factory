from __future__ import annotations

import httpx
import pytest
import respx

from app.services import agent_client as agent_client_module


@pytest.mark.asyncio
async def test_probe_agent_capabilities_reports_support() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(agent_client_module.OPENAI_PROBE_URL).mock(
            return_value=httpx.Response(401)
        )
        router.get(agent_client_module.GEMINI_PROBE_URL).mock(
            side_effect=httpx.ConnectError("boom")
        )
        router.get(agent_client_module.CLAUDE_PROBE_URL).mock(
            return_value=httpx.Response(403)
        )
        async with httpx.AsyncClient() as client:
            result = await agent_client_module.probe_agent_capabilities(client)

    assert result.supports_gpt is True
    assert result.supports_gemini is False
    assert result.supports_claude is True
    assert result.probe_latency_ms is not None

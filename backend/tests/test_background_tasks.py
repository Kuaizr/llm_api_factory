from __future__ import annotations

import logging

import pytest

from app.services.background_tasks import safe_create_task


@pytest.mark.asyncio
async def test_safe_create_task_logs_unhandled_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        task = safe_create_task(fail())
        with pytest.raises(RuntimeError):
            await task

    assert "Background task failed" in caplog.text
    assert "RuntimeError: boom" in caplog.text

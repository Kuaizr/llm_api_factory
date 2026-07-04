from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


def safe_create_task(coro: Awaitable[T]) -> asyncio.Task[T]:
    task = asyncio.create_task(coro)

    def _log_failure(completed: asyncio.Task[T]) -> None:
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Background task failed")

    task.add_done_callback(_log_failure)
    return task

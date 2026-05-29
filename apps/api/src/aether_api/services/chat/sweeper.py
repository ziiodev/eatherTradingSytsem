"""Background sweeper for orphaned in-flight chat messages.

The chat stream persists the assistant row at the END of the turn so a
mid-stream writer disconnect leaves an in-flight row with
``stop_reason IS NULL``. The sweeper marks those rows as
``stop_reason='aborted'`` after a configurable grace period so the UI
does not show them as still streaming forever.

The sweeper is started as a long-running asyncio Task from the FastAPI
lifespan handler and cancelled cleanly on shutdown. It MUST NOT take a
``user_id`` argument — the update predicate is global by design (the
``mark_aborted_stale`` repository method enforces no tenant predicate
intentionally; the sweeper runs with privileged process identity).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aether_api.repositories.chat_message_repository import ChatMessageRepository

logger = logging.getLogger(__name__)

#: How often the loop wakes up to check for stale rows.
DEFAULT_SLEEP_SECONDS: int = 60

#: How long an assistant row can sit with ``stop_reason IS NULL`` before
#: the sweeper marks it aborted. Five minutes is the design's default —
#: long enough to swallow brief reconnect blips, short enough that the
#: UI clears stuck rows in the same operator session.
DEFAULT_THRESHOLD_SECONDS: int = 300


async def chat_aborted_sweeper(
    session_factory: Callable[[], Any],
    *,
    sleep_seconds: int = DEFAULT_SLEEP_SECONDS,
    threshold_seconds: int = DEFAULT_THRESHOLD_SECONDS,
    now_factory: Callable[[], datetime] | None = None,
) -> None:
    """Loop forever (until cancelled), marking stale rows as aborted.

    Parameters
    ----------
    session_factory:
        Callable returning an async context manager that yields an
        :class:`AsyncSession`. In production this is the FastAPI app's
        global ``get_session_maker``.
    sleep_seconds:
        Inter-tick wait. Defaults to 60s.
    threshold_seconds:
        Wall-clock grace period before a row counts as stale. Defaults
        to 300s.
    now_factory:
        Optional injectable clock. Tests pass a frozen-time callable so
        the sweeper's notion of "now" can be controlled without
        monkey-patching :mod:`datetime`.

    Cancellation:
        The task is cancellable via ``task.cancel()``; the exception is
        re-raised after a final log so the runtime can observe it.
    """
    clock = now_factory or (lambda: datetime.now(tz=UTC))
    logger.info(
        "chat aborted sweeper started "
        "(sleep_seconds=%d, threshold_seconds=%d)",
        sleep_seconds,
        threshold_seconds,
    )
    try:
        while True:
            try:
                cutoff = clock() - timedelta(seconds=threshold_seconds)
                async with session_factory() as session:
                    repo = ChatMessageRepository(session)
                    count = await repo.mark_aborted_stale(older_than=cutoff)
                    if count:
                        await session.commit()
                        logger.info(
                            "chat sweeper marked %d stale assistant rows aborted "
                            "(cutoff=%s)",
                            count,
                            cutoff.isoformat(),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — keep the loop alive
                logger.exception("chat sweeper tick failed; will retry")

            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        logger.info("chat aborted sweeper cancelled; shutting down cleanly")
        raise


__all__ = [
    "DEFAULT_SLEEP_SECONDS",
    "DEFAULT_THRESHOLD_SECONDS",
    "chat_aborted_sweeper",
]

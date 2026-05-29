"""Tests for :mod:`aether_api.services.chat.sweeper`.

Covers:

* Fresh in-flight assistant row (< threshold) is left alone.
* Stale row (> threshold) is marked ``stop_reason='aborted'``.
* Cancellation propagates cleanly (CancelledError re-raised after a
  final log).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


async def _seed_chat_with_message(*, model: str = "claude-sonnet-4-5"):
    """Seed a project + conversation + ONE in-flight assistant row."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email="a@example.com", password="testtesttesttest")
        project = await seed_project(session, owner=user, name="proj-a")
        conv = await ChatConversationRepository(session).create(
            user_id=user.id, project_id=project.id, title="t"
        )
        msg = await ChatMessageRepository(session).insert_assistant(
            user_id=user.id,
            conversation_id=conv.id,
            content="partial",
            model=model,
            stop_reason=None,
        )
        await session.commit()
        return user.id, project.id, conv.id, msg.id


async def _set_created_at(message_id: uuid.UUID, when: datetime) -> None:
    """Back-date a message row so the sweeper's threshold can be exercised
    without waiting wall-clock seconds.
    """
    from aether_api.db.session import get_session_maker
    from sqlalchemy import text as sql_text

    maker = get_session_maker()
    async with maker() as session:
        await session.execute(
            sql_text(
                "UPDATE chat_messages SET created_at = :when WHERE id = :mid"
            ),
            {"when": when, "mid": message_id},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


async def test_sweeper_skips_fresh_rows(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )
    from aether_api.services.chat.sweeper import chat_aborted_sweeper

    user_id, _, conv_id, msg_id = await _seed_chat_with_message()
    # Row created_at = NOW(); cutoff would be NOW() - 5min — too old to match.
    maker = get_session_maker()

    async def one_tick(task: asyncio.Task) -> None:
        await asyncio.sleep(0)
        task.cancel()

    task = asyncio.create_task(
        chat_aborted_sweeper(
            maker,
            sleep_seconds=3600,  # never wakes again
            threshold_seconds=300,
        )
    )
    # Give the loop a beat to run one tick, then cancel.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with maker() as session:
        rows, _ = await ChatMessageRepository(session).list_for_conversation(
            user_id=user_id, conversation_id=conv_id
        )
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.stop_reason is None  # still in flight


async def test_sweeper_marks_stale_rows_aborted(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )
    from aether_api.services.chat.sweeper import chat_aborted_sweeper

    user_id, _, conv_id, msg_id = await _seed_chat_with_message()
    # Back-date the message 10 minutes — older than the 5-minute default.
    await _set_created_at(
        msg_id, datetime.now(tz=UTC) - timedelta(minutes=10)
    )

    maker = get_session_maker()
    task = asyncio.create_task(
        chat_aborted_sweeper(
            maker,
            sleep_seconds=3600,
            threshold_seconds=300,
        )
    )
    await asyncio.sleep(0.1)  # let one tick run
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with maker() as session:
        rows, _ = await ChatMessageRepository(session).list_for_conversation(
            user_id=user_id, conversation_id=conv_id
        )
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.stop_reason == "aborted"


async def test_sweeper_cancellation_is_clean(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.services.chat.sweeper import chat_aborted_sweeper

    maker = get_session_maker()
    task = asyncio.create_task(
        chat_aborted_sweeper(
            maker, sleep_seconds=3600, threshold_seconds=300
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() or task.done()


async def test_sweeper_uses_injected_clock(app_client) -> None:
    """The ``now_factory`` injection lets tests control the cutoff."""
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )
    from aether_api.services.chat.sweeper import chat_aborted_sweeper

    user_id, _, conv_id, msg_id = await _seed_chat_with_message()
    # Row created now. Inject a clock that lies — "now" is 1 hour in the
    # future. The threshold of 5 minutes makes the row clearly stale.
    maker = get_session_maker()
    far_future = datetime.now(tz=UTC) + timedelta(hours=1)

    task = asyncio.create_task(
        chat_aborted_sweeper(
            maker,
            sleep_seconds=3600,
            threshold_seconds=300,
            now_factory=lambda: far_future,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with maker() as session:
        rows, _ = await ChatMessageRepository(session).list_for_conversation(
            user_id=user_id, conversation_id=conv_id
        )
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.stop_reason == "aborted"

"""Tests for :class:`ChatMessageRepository` — tenant-scoped transitively.

Cross-tenant probes return ``None`` / empty / 0 rows (or raise
``PermissionError`` on inserts). ``mark_aborted_stale`` is the sweeper
primitive — exercised here for precision (only the matching rows
flip).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_users_with_projects_and_conversations():
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user_a = await seed_user(session, email="a@example.com", password="testtesttesttest")
        user_b = await seed_user(session, email="b@example.com", password="testtesttesttest")
        proj_a = await seed_project(session, owner=user_a, name="proj-a")
        proj_b = await seed_project(session, owner=user_b, name="proj-b")
        repo = ChatConversationRepository(session)
        conv_a = await repo.create(user_id=user_a.id, project_id=proj_a.id, title="a")
        conv_b = await repo.create(user_id=user_b.id, project_id=proj_b.id, title="b")
        await session.commit()
        return user_a.id, user_b.id, conv_a.id, conv_b.id


# ---------------------------------------------------------------------------
# insert_user / insert_assistant / insert_tool — happy paths
# ---------------------------------------------------------------------------


async def test_insert_user_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    user_a_id, _, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        msg = await repo.insert_user(
            user_id=user_a_id,
            conversation_id=conv_a_id,
            content="hello",
        )
        await session.commit()
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.tool_calls is None
        assert msg.tool_results is None
        assert msg.stop_reason is None


async def test_insert_assistant_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    user_a_id, _, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        msg = await repo.insert_assistant(
            user_id=user_a_id,
            conversation_id=conv_a_id,
            content="reply",
            tool_calls=[
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "get_project_status",
                    "input": {},
                }
            ],
            tokens_in=42,
            tokens_out=17,
            model="claude-sonnet-4-5-20250929",
            stop_reason="end_turn",
            meta_data={"thinking": [{"type": "thinking", "thinking": "..."}]},
        )
        await session.commit()
        assert msg.role == "assistant"
        assert msg.tool_calls is not None
        assert msg.tool_calls[0]["name"] == "get_project_status"
        assert msg.tokens_in == 42
        assert msg.tokens_out == 17
        assert msg.model == "claude-sonnet-4-5-20250929"
        assert msg.stop_reason == "end_turn"
        assert "thinking" in msg.meta_data


async def test_insert_tool_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    user_a_id, _, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        msg = await repo.insert_tool(
            user_id=user_a_id,
            conversation_id=conv_a_id,
            tool_results=[
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": "ok",
                }
            ],
        )
        await session.commit()
        assert msg.role == "tool"
        assert msg.content == ""
        assert msg.tool_results is not None
        assert msg.tool_results[0]["tool_use_id"] == "tu_1"


# ---------------------------------------------------------------------------
# list_for_conversation
# ---------------------------------------------------------------------------


async def test_list_for_conversation_chronological(app_client) -> None:
    # Each insert is committed in its own transaction so ``NOW()``
    # returns a fresh ``transaction_timestamp()`` per row. Within a
    # single transaction Postgres pins ``NOW()`` to the txn start,
    # so multiple inserts would tie on ``created_at`` and the
    # secondary ``id`` ordering (random UUIDs) would scramble the
    # logical order. Real chat usage commits each turn separately,
    # which the test mirrors.
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    user_a_id, _, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        await repo.insert_user(user_id=user_a_id, conversation_id=conv_a_id, content="1")
        await session.commit()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        await repo.insert_assistant(
            user_id=user_a_id, conversation_id=conv_a_id, content="2"
        )
        await session.commit()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        await repo.insert_user(user_id=user_a_id, conversation_id=conv_a_id, content="3")
        await session.commit()

    async with maker() as session:
        repo = ChatMessageRepository(session)
        rows, total = await repo.list_for_conversation(
            user_id=user_a_id, conversation_id=conv_a_id, limit=10, offset=0
        )
        assert total == 3
        assert [m.content for m in rows] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# Cross-tenant probes
# ---------------------------------------------------------------------------


async def test_insert_user_cross_tenant_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    _, user_b_id, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert_user(
                user_id=user_b_id, conversation_id=conv_a_id, content="evil"
            )


async def test_insert_assistant_cross_tenant_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    _, user_b_id, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert_assistant(
                user_id=user_b_id, conversation_id=conv_a_id, content="evil"
            )


async def test_insert_tool_cross_tenant_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    _, user_b_id, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        with pytest.raises(PermissionError):
            await repo.insert_tool(
                user_id=user_b_id,
                conversation_id=conv_a_id,
                tool_results=[],
            )


async def test_list_for_conversation_cross_tenant_empty(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )

    user_a_id, user_b_id, conv_a_id, _ = (
        await _seed_two_users_with_projects_and_conversations()
    )

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        await repo.insert_user(
            user_id=user_a_id, conversation_id=conv_a_id, content="secret"
        )
        await session.commit()

    async with maker() as session:
        repo = ChatMessageRepository(session)
        rows, total = await repo.list_for_conversation(
            user_id=user_b_id, conversation_id=conv_a_id, limit=10, offset=0
        )
        assert rows == []
        assert total == 0


# ---------------------------------------------------------------------------
# mark_aborted_stale — precision: only matching rows flip.
# ---------------------------------------------------------------------------


async def test_mark_aborted_stale_only_matches_stale_in_flight_assistant(
    app_client,
) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.models.chat_message import ChatMessage
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )
    from sqlalchemy import select, update

    user_a_id, _, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        # Stale assistant — should flip.
        stale_assistant = await repo.insert_assistant(
            user_id=user_a_id, conversation_id=conv_a_id, content="stale"
        )
        # Fresh assistant — should NOT flip (newer than cutoff).
        fresh_assistant = await repo.insert_assistant(
            user_id=user_a_id, conversation_id=conv_a_id, content="fresh"
        )
        # Stale assistant that already has stop_reason — should NOT flip
        # (predicate filters stop_reason IS NULL).
        finished_assistant = await repo.insert_assistant(
            user_id=user_a_id,
            conversation_id=conv_a_id,
            content="done",
            stop_reason="end_turn",
        )
        # Stale user turn — should NOT flip (role filter).
        stale_user = await repo.insert_user(
            user_id=user_a_id, conversation_id=conv_a_id, content="hello"
        )
        await session.commit()

        # Backdate stale_assistant, finished_assistant, stale_user.
        old = datetime.now(tz=UTC) - timedelta(hours=2)
        await session.execute(
            update(ChatMessage)
            .where(
                ChatMessage.id.in_(
                    [stale_assistant.id, finished_assistant.id, stale_user.id]
                )
            )
            .values(created_at=old)
        )
        await session.commit()

    cutoff = datetime.now(tz=UTC) - timedelta(hours=1)

    async with maker() as session:
        repo = ChatMessageRepository(session)
        n = await repo.mark_aborted_stale(older_than=cutoff)
        await session.commit()
        assert n == 1

    async with maker() as session:
        result = await session.execute(
            select(ChatMessage.id, ChatMessage.stop_reason).where(
                ChatMessage.id.in_(
                    [
                        stale_assistant.id,
                        fresh_assistant.id,
                        finished_assistant.id,
                        stale_user.id,
                    ]
                )
            )
        )
        by_id = {row[0]: row[1] for row in result.all()}
        assert by_id[stale_assistant.id] == "aborted"
        assert by_id[fresh_assistant.id] is None
        assert by_id[finished_assistant.id] == "end_turn"
        # The user row never had a stop_reason and must still be NULL.
        assert by_id[stale_user.id] is None


async def test_mark_aborted_stale_idempotent(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.models.chat_message import ChatMessage
    from aether_api.repositories.chat_message_repository import (
        ChatMessageRepository,
    )
    from sqlalchemy import update

    user_a_id, _, conv_a_id, _ = await _seed_two_users_with_projects_and_conversations()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        msg = await repo.insert_assistant(
            user_id=user_a_id, conversation_id=conv_a_id, content="stale"
        )
        await session.commit()

        old = datetime.now(tz=UTC) - timedelta(hours=2)
        await session.execute(
            update(ChatMessage).where(ChatMessage.id == msg.id).values(created_at=old)
        )
        await session.commit()

    cutoff = datetime.now(tz=UTC) - timedelta(hours=1)

    async with maker() as session:
        repo = ChatMessageRepository(session)
        n1 = await repo.mark_aborted_stale(older_than=cutoff)
        await session.commit()
        assert n1 == 1

    async with maker() as session:
        repo = ChatMessageRepository(session)
        n2 = await repo.mark_aborted_stale(older_than=cutoff)
        await session.commit()
        # Already aborted — predicate excludes it now (stop_reason IS NOT NULL).
        assert n2 == 0

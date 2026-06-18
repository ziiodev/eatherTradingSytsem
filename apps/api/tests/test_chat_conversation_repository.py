"""Tests for :class:`ChatConversationRepository` — tenant-scoped via projects JOIN.

Cross-tenant probes return ``None`` / empty / 0 rows (or refuse writes
with ``PermissionError``) — never confirm existence to a non-owner.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


async def _seed_two_users_with_projects():
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user_a = await seed_user(session, email="a@example.com", password="testtesttesttest")
        user_b = await seed_user(session, email="b@example.com", password="testtesttesttest")
        proj_a = await seed_project(session, owner=user_a, name="proj-a")
        proj_b = await seed_project(session, owner=user_b, name="proj-b")
        await session.commit()
        return user_a.id, user_b.id, proj_a.id, proj_b.id


# ---------------------------------------------------------------------------
# create + get (happy path)
# ---------------------------------------------------------------------------


async def test_create_and_get_happy_path(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(
            user_id=user_a_id,
            project_id=proj_a_id,
            title="Strategy review",
        )
        await session.commit()

        assert conv.title == "Strategy review"
        assert conv.pair_id == proj_a_id
        assert conv.user_id == user_a_id
        assert conv.archived_at is None
        assert conv.tokens_in_total == 0
        assert conv.usd_estimated_total == Decimal("0")

    async with maker() as session:
        repo = ChatConversationRepository(session)
        got = await repo.get(user_id=user_a_id, conversation_id=conv.id)
        assert got is not None
        assert got.id == conv.id


async def test_create_with_model_override_lands_in_meta_data(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(
            user_id=user_a_id,
            project_id=proj_a_id,
            model_override="claude-opus-4-7",
        )
        await session.commit()
        assert conv.meta_data == {"model_override": "claude-opus-4-7"}


async def test_create_default_title_when_omitted(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id)
        await session.commit()
        # The DB server_default '(sin título)' kicks in.
        assert conv.title == "(sin título)"


# ---------------------------------------------------------------------------
# Cross-tenant probes
# ---------------------------------------------------------------------------


async def test_create_cross_tenant_raises(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    _, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        with pytest.raises(PermissionError):
            await repo.create(
                user_id=user_b_id,
                project_id=proj_a_id,
                title="evil",
            )


async def test_get_cross_tenant_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id)
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        got = await repo.get(user_id=user_b_id, conversation_id=conv.id)
        assert got is None


async def test_list_for_project_cross_tenant_empty(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        await repo.create(user_id=user_a_id, project_id=proj_a_id, title="x")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        rows, total = await repo.list_for_project(
            user_id=user_b_id, project_id=proj_a_id, limit=10, offset=0
        )
        assert rows == []
        assert total == 0


# ---------------------------------------------------------------------------
# list_for_project — archived filter
# ---------------------------------------------------------------------------


async def test_list_for_project_filters_archived(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        live = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="live")
        dead = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="dead")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        archived = await repo.archive(user_id=user_a_id, conversation_id=dead.id)
        await session.commit()
        assert archived is not None
        assert archived.archived_at is not None

    async with maker() as session:
        repo = ChatConversationRepository(session)
        live_rows, live_total = await repo.list_for_project(
            user_id=user_a_id, project_id=proj_a_id, limit=10, offset=0
        )
        assert {c.id for c in live_rows} == {live.id}
        assert live_total == 1

        archived_rows, archived_total = await repo.list_for_project(
            user_id=user_a_id,
            project_id=proj_a_id,
            archived=True,
            limit=10,
            offset=0,
        )
        assert {c.id for c in archived_rows} == {dead.id}
        assert archived_total == 1


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------


async def test_archive_sets_archived_at(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="x")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        archived = await repo.archive(user_id=user_a_id, conversation_id=conv.id)
        await session.commit()
        assert archived is not None
        assert archived.archived_at is not None


async def test_archive_cross_tenant_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="x")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        result = await repo.archive(user_id=user_b_id, conversation_id=conv.id)
        await session.commit()
        assert result is None


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


async def test_rename_updates_title_and_updated_at(app_client) -> None:
    import asyncio

    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="old")
        await session.commit()
        old_updated_at = conv.updated_at

    # Pause so NOW() is observably later than the initial timestamp.
    await asyncio.sleep(0.01)

    async with maker() as session:
        repo = ChatConversationRepository(session)
        renamed = await repo.rename(
            user_id=user_a_id, conversation_id=conv.id, title="new"
        )
        await session.commit()
        assert renamed is not None
        assert renamed.title == "new"
        assert renamed.updated_at >= old_updated_at


async def test_rename_cross_tenant_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="x")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        result = await repo.rename(
            user_id=user_b_id, conversation_id=conv.id, title="hijack"
        )
        await session.commit()
        assert result is None

    # Confirm the original title is untouched.
    async with maker() as session:
        repo = ChatConversationRepository(session)
        got = await repo.get(user_id=user_a_id, conversation_id=conv.id)
        assert got is not None
        assert got.title == "x"


# ---------------------------------------------------------------------------
# increment_tokens
# ---------------------------------------------------------------------------


async def test_increment_tokens_accumulates(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, _, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="x")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        updated = await repo.increment_tokens(
            user_id=user_a_id,
            conversation_id=conv.id,
            tokens_in_delta=123,
            usd_delta=Decimal("0.005"),
        )
        await session.commit()
        assert updated is not None
        assert updated.tokens_in_total == 123
        assert updated.usd_estimated_total == Decimal("0.005000")

    async with maker() as session:
        repo = ChatConversationRepository(session)
        updated = await repo.increment_tokens(
            user_id=user_a_id,
            conversation_id=conv.id,
            tokens_in_delta=77,
            usd_delta=Decimal("0.003"),
        )
        await session.commit()
        assert updated is not None
        assert updated.tokens_in_total == 200
        assert updated.usd_estimated_total == Decimal("0.008000")


async def test_increment_tokens_cross_tenant_returns_none(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    user_a_id, user_b_id, proj_a_id, _ = await _seed_two_users_with_projects()

    maker = get_session_maker()
    async with maker() as session:
        repo = ChatConversationRepository(session)
        conv = await repo.create(user_id=user_a_id, project_id=proj_a_id, title="x")
        await session.commit()

    async with maker() as session:
        repo = ChatConversationRepository(session)
        result = await repo.increment_tokens(
            user_id=user_b_id,
            conversation_id=conv.id,
            tokens_in_delta=999,
            usd_delta=Decimal("1.0"),
        )
        await session.commit()
        assert result is None

    # And confirm the rollups stayed at zero (no silent write happened).
    async with maker() as session:
        repo = ChatConversationRepository(session)
        got = await repo.get(user_id=user_a_id, conversation_id=conv.id)
        assert got is not None
        assert got.tokens_in_total == 0
        assert got.usd_estimated_total == Decimal("0")

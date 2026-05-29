"""Tests for :class:`ChatActionProposalRepository` — SCHEMA-ONLY in v1.

The repository module must import cleanly so the v1 application can
wire dependency-injection without a circular failure; calling
``insert`` MUST raise :class:`NotImplementedError` so any code path
that lands there is loud about violating the v1 read-only contract.
"""

from __future__ import annotations

import uuid

import pytest


def test_module_imports_cleanly() -> None:
    from aether_api.repositories.chat_action_proposal_repository import (
        ChatActionProposalRepository,
    )

    assert ChatActionProposalRepository is not None
    # And the model is wired to the right ORM class.
    from aether_api.models.chat_action_proposal import ChatActionProposal

    assert ChatActionProposalRepository.model is ChatActionProposal


async def test_insert_raises_not_implemented() -> None:
    from aether_api.repositories.chat_action_proposal_repository import (
        ChatActionProposalRepository,
    )

    # Session is unused — the NotImplementedError fires before any SQL
    # is executed. ``None`` is fine here because the body never
    # dereferences ``self.session``.
    repo = ChatActionProposalRepository(session=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match="project-chat-actions"):
        await repo.insert(
            user_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            tool_name="submit_order",
            payload={"foo": "bar"},
        )

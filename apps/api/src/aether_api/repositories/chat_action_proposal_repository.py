"""``chat_action_proposals`` data access — SCHEMA-ONLY placeholder in v1.

The ``chat_action_proposals`` table is created by migration 0014_chat
so a follow-up migration is not required when the deferred sibling
change ``project-chat-actions`` ships. The repository surface lives in
this module so application imports and dependency wiring can resolve
``ChatActionProposalRepository`` symbols today; every mutating method
raises :class:`NotImplementedError` to make the v1 read-only contract
explicit at the call site.

The sibling change ``project-chat-actions`` replaces this module with
a real implementation (insert / decide / list / mark_expired etc.).
"""

from __future__ import annotations

import uuid
from typing import Any

from aether_api.models.chat_action_proposal import ChatActionProposal
from aether_api.repositories.base import BaseRepository


class ChatActionProposalRepository(BaseRepository):
    model = ChatActionProposal

    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        project_id: uuid.UUID,
        tool_name: str,
        payload: dict[str, Any],
    ) -> ChatActionProposal:
        """Refuses in v1 — write tools are not exposed.

        The ``project-chat-actions`` sibling change replaces this
        placeholder with a real implementation. Until then any caller
        that lands here is using a code path the v1 contract forbids.
        """
        raise NotImplementedError(
            "populated by project-chat-actions sibling change"
        )

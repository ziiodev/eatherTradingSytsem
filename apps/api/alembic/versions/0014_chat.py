"""0014_chat — project-scoped conversational surface (chat plane).

Lands three new tables for the v1 ``project-chat`` change:

* ``chat_conversations``     — one row per logical conversation between
                               the operator and Claude within a project.
                               Carries running token / USD counters so a
                               per-conversation budget gate is a single
                               column read (no aggregation over messages).
* ``chat_messages``          — append-only turn history. Includes
                               assistant tool-use payloads
                               (``tool_calls``) and the corresponding
                               tool result blocks (``tool_results``), per-
                               turn token usage, model, stop_reason and a
                               forward-compatible ``action_proposal``
                               JSONB column that stays NULL in v1 (the
                               sibling change ``project-chat-actions``
                               will populate it). ``meta_data`` carries
                               Anthropic thinking blocks (extended-thinking
                               replays) and any other per-turn structured
                               extras.
* ``chat_action_proposals``  — side table for action approvals.
                               Schema-only in v1 — NO writes happen until
                               the deferred ``project-chat-actions``
                               change ships. Pre-installing the table
                               eliminates a follow-up migration for that
                               change.

Multi-tenancy: none of these tables carry a ``user_id`` column. Tenant
isolation flows transitively through ``projects.user_id`` (see the
``multi-tenancy-delta`` spec). Repositories MUST scope every read /
write via a JOIN through ``projects``.

Cascades:

* ``chat_conversations.project_id`` → ``projects.id`` ON DELETE CASCADE.
  Hard-deleting a project (admin path) takes its conversations with it.
* ``chat_messages.conversation_id`` → ``chat_conversations.id`` ON DELETE
  CASCADE. Same shape — deleting a conversation removes its history.
* ``chat_action_proposals.message_id`` / ``conversation_id`` /
  ``project_id`` all CASCADE so a hard-delete at any level is clean.
* ``chat_conversations.user_id`` → ``users.id`` ON DELETE RESTRICT
  (denormalised for the user-scoped index — same pattern as ``orders``
  / ``sleep_runs``). The transitive tenant check still hits
  ``projects.user_id`` in repositories; this column exists to drive
  the ``(user_id, created_at) WHERE archived_at IS NULL`` partial index.

Indexes:

* ``idx_chat_conv_project_created`` partial — ``(project_id, created_at
  DESC) WHERE archived_at IS NULL``. Drives the project's "active
  conversations" sidebar list — the most common read by far.
* ``idx_chat_conv_user_created`` partial — ``(user_id, created_at DESC)
  WHERE archived_at IS NULL``. For an operator's "across-projects
  recent activity" view (deferred UI, but the index lands here).
* ``idx_chat_msg_conv_created`` — ``(conversation_id, created_at)``.
  Drives the turn-by-turn replay in the conversation view.

``action_proposal JSONB NULL`` on ``chat_messages`` is the inline
forward-compat slot: in v1 it is ALWAYS NULL because the dispatcher
exposes read-only tools. The sibling change activates writes by
flipping the column from NULL to a structured payload.

Downgrade drops in reverse FK order:
    chat_action_proposals → chat_messages → chat_conversations

Revision ID: 0014_chat
Revises: 0013_operativa_orders_extend
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0014_chat"
down_revision: str | Sequence[str] | None = "0013_operativa_orders_extend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # ------------------------------------------------------------------
    # chat_conversations — one row per operator↔assistant thread.
    #
    # ``tokens_in_total`` and ``usd_estimated_total`` are RUNNING
    # counters maintained by the chat service after each assistant turn
    # (atomic UPDATE with ``+ :delta`` in the repository). Keeping the
    # rollup on the conversation row makes "is this conversation over
    # budget?" a single ``WHERE id = ?`` lookup instead of a SUM over
    # ``chat_messages``.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- CASCADE: hard-delete of a project takes its conversations.
            -- Repository layer enforces tenant isolation via a JOIN
            -- through ``projects.user_id``.
            project_id           UUID NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,

            -- RESTRICT mirrors the canonical ``orders`` / ``sleep_runs``
            -- pattern. The transitive owner is ``projects.user_id``;
            -- this denormalised column powers the user-scoped partial
            -- index without forcing every list query to join projects.
            user_id              UUID NOT NULL
                REFERENCES users(id) ON DELETE RESTRICT,

            title                VARCHAR(200) NOT NULL DEFAULT '(sin título)',

            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            archived_at          TIMESTAMPTZ NULL,

            meta_data            JSONB NOT NULL DEFAULT '{}'::jsonb,

            -- Running rollups maintained by the chat service.
            tokens_in_total      INTEGER NOT NULL DEFAULT 0,
            usd_estimated_total  NUMERIC(12,6) NOT NULL DEFAULT 0,

            CONSTRAINT chat_conversations_title_nonempty
                CHECK (length(title) >= 1),
            CONSTRAINT chat_conversations_tokens_nonneg
                CHECK (tokens_in_total >= 0),
            CONSTRAINT chat_conversations_usd_nonneg
                CHECK (usd_estimated_total >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_conv_project_created "
        "ON chat_conversations(project_id, created_at DESC) "
        "WHERE archived_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_conv_user_created "
        "ON chat_conversations(user_id, created_at DESC) "
        "WHERE archived_at IS NULL"
    )

    # ------------------------------------------------------------------
    # chat_messages — append-only turn history.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- CASCADE: deleting a conversation removes its history.
            conversation_id   UUID NOT NULL
                REFERENCES chat_conversations(id) ON DELETE CASCADE,

            -- Closed enum — CHECK is the source of truth.
            role              VARCHAR(20) NOT NULL,

            -- Content body. For ``user`` / ``assistant`` / ``system``
            -- this is the visible text; for ``tool`` it may also carry
            -- a JSON-encoded preview the dashboard renders.
            content           TEXT NOT NULL,

            -- Assistant tool-use blocks (Anthropic format) — list of
            -- {type:'tool_use', id, name, input}. NULL for non-tool
            -- assistant turns and for every non-assistant role.
            tool_calls        JSONB NULL,

            -- Matching tool-result blocks — list of {type:'tool_result',
            -- tool_use_id, content}. NULL outside the ``tool`` role.
            tool_results      JSONB NULL,

            -- Per-turn token usage as reported by Anthropic. Both NULL
            -- on rows we never sent upstream (user / tool turns).
            tokens_in         INTEGER NULL,
            tokens_out        INTEGER NULL,

            -- Anthropic model id (e.g. ``claude-sonnet-4-5-20250929``)
            -- frozen at the moment the assistant turn was issued.
            model             VARCHAR(50) NULL,

            -- Anthropic stop_reason (``end_turn`` / ``tool_use`` /
            -- ``max_tokens`` / ``stop_sequence`` / sweeper-only
            -- ``aborted``). NULL until the assistant turn is finalised.
            stop_reason       VARCHAR(50) NULL,

            -- Forward-compat slot. ALWAYS NULL in v1 (the dispatcher is
            -- read-only). Populated by the deferred sibling change
            -- ``project-chat-actions`` once write-tool approvals land.
            action_proposal   JSONB NULL,

            -- Anthropic ``thinking`` blocks (extended thinking) plus any
            -- other per-turn structured extras land here.
            meta_data         JSONB NOT NULL DEFAULT '{}'::jsonb,

            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT chat_messages_role_valid
                CHECK (role IN ('user', 'assistant', 'system', 'tool')),
            CONSTRAINT chat_messages_tokens_in_nonneg
                CHECK (tokens_in IS NULL OR tokens_in >= 0),
            CONSTRAINT chat_messages_tokens_out_nonneg
                CHECK (tokens_out IS NULL OR tokens_out >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_msg_conv_created "
        "ON chat_messages(conversation_id, created_at)"
    )

    # ------------------------------------------------------------------
    # chat_action_proposals — SCHEMA-ONLY in v1. No writes happen until
    # the deferred ``project-chat-actions`` change ships. Pre-installing
    # eliminates a follow-up migration there.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_action_proposals (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- The assistant message that proposed the action. CASCADE
            -- so a wiped message takes its proposal with it.
            message_id        UUID NOT NULL
                REFERENCES chat_messages(id) ON DELETE CASCADE,

            -- Denormalised pointers for fast filtering. Both CASCADE
            -- so hard-delete at any level is clean.
            conversation_id   UUID NOT NULL
                REFERENCES chat_conversations(id) ON DELETE CASCADE,
            project_id        UUID NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,

            -- Tool the assistant wanted to invoke (e.g. ``submit_order``).
            tool_name         VARCHAR(80) NOT NULL,

            -- Validated payload (already shape-checked by the dispatcher
            -- before insert — proposals never carry raw LLM JSON).
            payload           JSONB NOT NULL,

            -- Status lifecycle. ``pending`` is the only legal initial
            -- state; the others are terminal.
            status            VARCHAR(20) NOT NULL DEFAULT 'pending',

            -- Operator decision metadata.
            decided_at        TIMESTAMPTZ NULL,
            decided_by        UUID NULL
                REFERENCES users(id) ON DELETE SET NULL,
            decision_note     TEXT NULL,

            -- Tool execution outcome (filled when the dispatched tool
            -- returns — NULL while pending or after a rejection).
            executed_at       TIMESTAMPTZ NULL,
            execution_result  JSONB NULL,

            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT chat_action_proposals_status_valid
                CHECK (status IN (
                    'pending', 'approved', 'rejected', 'expired', 'executed'
                )),
            CONSTRAINT chat_action_proposals_tool_name_nonempty
                CHECK (length(tool_name) >= 1)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_action_proposals_conv "
        "ON chat_action_proposals(conversation_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_action_proposals_status "
        "ON chat_action_proposals(project_id, status) "
        "WHERE status = 'pending'"
    )

    # ------------------------------------------------------------------
    # Append-only grants for the existing ``aether`` role. Mirrors the
    # pattern used by 0011 / sleep-learning so a fresh role-bootstrap
    # only needs to look at one migration to learn the access shape.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON chat_conversations TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON chat_messages TO aether';
                EXECUTE 'GRANT INSERT, SELECT, UPDATE ON chat_action_proposals TO aether';
                EXECUTE 'GRANT DELETE ON chat_conversations TO aether';
                EXECUTE 'GRANT DELETE ON chat_messages TO aether';
                EXECUTE 'GRANT DELETE ON chat_action_proposals TO aether';
            END IF;
        END
        $$;
        """
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse FK order: chat_action_proposals → chat_messages → chat_conversations.
    op.execute("DROP INDEX IF EXISTS idx_chat_action_proposals_status")
    op.execute("DROP INDEX IF EXISTS idx_chat_action_proposals_conv")
    op.execute("DROP TABLE IF EXISTS chat_action_proposals")

    op.execute("DROP INDEX IF EXISTS idx_chat_msg_conv_created")
    op.execute("DROP TABLE IF EXISTS chat_messages")

    op.execute("DROP INDEX IF EXISTS idx_chat_conv_user_created")
    op.execute("DROP INDEX IF EXISTS idx_chat_conv_project_created")
    op.execute("DROP TABLE IF EXISTS chat_conversations")

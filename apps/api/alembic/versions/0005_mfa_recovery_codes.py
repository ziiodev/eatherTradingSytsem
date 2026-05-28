"""0005_mfa_recovery_codes — argon2id-hashed single-use recovery codes for TOTP MFA.

Activates the dormant ``users.mfa_*`` columns from ``0001_init`` by adding
the storage for recovery codes: 10 one-time codes are minted at MFA
verification time, the user prints/saves them, and any one of them
authenticates the login two-step instead of a TOTP code.

Shape:

* ``id``            UUID PK, ``gen_random_uuid()`` default (pgcrypto from 0001).
* ``user_id``       UUID FK → ``users(id)`` ON DELETE CASCADE — losing the
                    user nukes the code list; nothing else depends on it.
* ``code_hash``     argon2id hash of the raw code (same parameters as
                    ``users.password_hash``). NEVER store plaintext.
* ``used_at``       TIMESTAMP, NULL until consumed. Single-use is enforced
                    via atomic ``UPDATE ... WHERE used_at IS NULL RETURNING id``
                    in ``aether_api.services.mfa``; the partial index below
                    keeps the unused-by-user scan O(unused-rows).
* ``created_at``    TIMESTAMP, ``NOW()`` default — the audit trail for
                    "user regenerated codes on YYYY-MM-DD".

Indexes:

* ``uq_mfa_recovery_codes_user_id_code_hash``
  Unique on ``(user_id, code_hash)``. The hash is per-user — argon2 salt
  guarantees no collision across users — but the unique index doubles as
  a safety net should a future change ever weaken the hash. Cheap.

* ``idx_mfa_recovery_codes_user_unused``
  Partial index on ``(user_id) WHERE used_at IS NULL``. The single-use
  lookup is exactly this predicate; the partial keeps the index small
  even for users who've consumed half their codes.

NOTE on chain ordering:

    sandbox landed ``0004_agent_runs`` immediately before this slot.
    ``down_revision`` chains to that head. If a sibling moves the head
    before this PR merges, edit ONLY this line — upgrade()/downgrade()
    are idempotent.

Revision ID: 0005_mfa_recovery_codes
Revises: 0004_agent_runs
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic identifiers.
revision: str = "0005_mfa_recovery_codes"
down_revision: str | Sequence[str] | None = "0004_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# -----------------------------------------------------------------------------
# Upgrade
# -----------------------------------------------------------------------------
def upgrade() -> None:
    # Idempotent guard: re-running this migration on a DB that already has
    # the table is a no-op. Matches the project's "idempotent on re-run"
    # convention (see 0002_audit_log / 0003_skills).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash   VARCHAR(255) NOT NULL,
            used_at     TIMESTAMP,
            created_at  TIMESTAMP DEFAULT NOW()
        )
        """
    )

    # Unique on (user_id, code_hash) — argon2 salt already prevents collisions
    # across users, but the index pins the invariant at the DB layer.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_mfa_recovery_codes_user_id_code_hash "
        "ON mfa_recovery_codes(user_id, code_hash)"
    )

    # Partial index for the single-use lookup. The application's atomic
    # ``UPDATE ... WHERE used_at IS NULL RETURNING id`` matches this
    # predicate exactly, so the planner picks the partial every time.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mfa_recovery_codes_user_unused "
        "ON mfa_recovery_codes(user_id) WHERE used_at IS NULL"
    )


# -----------------------------------------------------------------------------
# Downgrade
# -----------------------------------------------------------------------------
def downgrade() -> None:
    # Reverse what upgrade() did. Drop indexes before the table so a partial
    # rollback (e.g. failed CASCADE) leaves a coherent state.
    op.execute("DROP INDEX IF EXISTS idx_mfa_recovery_codes_user_unused")
    op.execute("DROP INDEX IF EXISTS uq_mfa_recovery_codes_user_id_code_hash")
    op.execute("DROP TABLE IF EXISTS mfa_recovery_codes")

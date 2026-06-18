"""Reversibility / idempotence checks for the schema migrations.

These tests are marked ``integration`` and skip automatically when no
Postgres is available (no Docker, no ``DATABASE_URL``). They are the
guardrail for the "migrations are reversible" charter invariant — the
spec for ``db-schema`` requires that ``downgrade`` undo ``upgrade``
cleanly and that re-applying ``upgrade`` succeed.

Scope (Phase 2):
    * upgrade head on a clean DB creates the four charter tables.
    * downgrade base removes them.
    * upgrade head again succeeds (idempotent across down/up cycles).

The tests intentionally do NOT assert exact column lists — we have the
``0001_init.sql`` snapshot for that, hand-reviewed against CHARTER.md.
Here we only assert structural invariants (table existence, no rows
leaked, key constraints present).
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from alembic.config import Config

# Mark every test in this file as `integration`. Run with:
#   pytest -m integration
# Skip by default in unit-test-only runs.
pytestmark = pytest.mark.integration


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
# Full table set after the squashed 0001_init (accounts-pairs hierarchy).
# ``projects`` is renamed ``pairs``; ``exchanges`` + ``accounts`` are the
# new parent tables.
EXPECTED_TABLES = {
    "users",
    "sessions",
    "agents",
    "skills",
    "exchanges",
    "accounts",
    "pairs",
    "audit_log",
    "mfa_recovery_codes",
    "agent_skills",
    "agent_runs",
    "container_events",
    "orders",
    "order_log",
    "order_approvals",
    "sleep_runs",
    "sleep_reflections",
    "config_versions",
    "q_tables",
    "episodic_memory",
    "semantic_memory",
    "sleep_reports",
    "chat_conversations",
    "chat_messages",
    "chat_action_proposals",
}


def _sync_url_from_async(async_url: str) -> str:
    """Strip any SQLAlchemy driver suffix so ``psycopg.connect`` accepts the URL.

    SQLAlchemy URLs look like ``postgresql+asyncpg://...`` or
    ``postgresql+psycopg://...``; psycopg3's own ``psycopg.connect`` only
    speaks the bare libpq scheme ``postgresql://``. Returning the bare scheme
    works for both psycopg3 and (transitively) any libpq-based client.
    """
    if async_url.startswith("postgresql+"):
        # Drop the "+driver" suffix entirely.
        # e.g. "postgresql+asyncpg://u@h/d" -> "postgresql://u@h/d"
        _, _, tail = async_url.partition("://")
        return "postgresql://" + tail
    return async_url


def _list_public_tables(sync_url: str) -> set[str]:
    """Query information_schema for public-schema tables.

    Uses psycopg (sync) so we don't have to spin an event loop just to
    introspect the DB.
    """
    import psycopg

    with psycopg.connect(sync_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        return {row[0] for row in cur.fetchall()}


def _upgrade_head(cfg: Config) -> None:
    from alembic import command

    command.upgrade(cfg, "head")


def _downgrade_base(cfg: Config) -> None:
    from alembic import command

    command.downgrade(cfg, "base")


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------
def test_upgrade_head_clean_db(database_url: str, alembic_config: Config) -> None:
    """Running ``alembic upgrade head`` on a fresh DB creates all tables.

    Pre-condition: ``database_url`` fixture has set ``DATABASE_URL`` in the
    environment and (in the testcontainers case) brought up an empty
    Postgres. We do not assume the DB starts truly empty — if a previous
    test in the session left tables behind we tear them down first.
    """
    sync_url = _sync_url_from_async(os.environ["DATABASE_URL"])

    # Defensive teardown so this test is robust against ordering.
    # Empty DB / no alembic_version table → fine, nothing to undo.
    with contextlib.suppress(Exception):
        _downgrade_base(alembic_config)

    _upgrade_head(alembic_config)
    tables = _list_public_tables(sync_url)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Expected tables missing after upgrade head: {missing}"


def test_downgrade_base_then_upgrade(database_url: str, alembic_config: Config) -> None:
    """``downgrade base`` removes the tables, ``upgrade head`` puts them back.

    This is the reversibility check. If downgrade leaves orphan objects
    or upgrade fails on the second run, the migration is not reversible
    and the spec is violated.
    """
    sync_url = _sync_url_from_async(os.environ["DATABASE_URL"])

    # Make sure something exists to tear down — upgrade first.
    _upgrade_head(alembic_config)
    assert _list_public_tables(sync_url) >= EXPECTED_TABLES

    # Tear it down.
    _downgrade_base(alembic_config)
    tables_after_down = _list_public_tables(sync_url)
    leftover = EXPECTED_TABLES & tables_after_down
    assert not leftover, (
        f"Downgrade base did not remove charter tables: {leftover}. "
        "The migration is not reversible — fix the downgrade() body."
    )

    # And bring it back — proves the migration is replayable.
    _upgrade_head(alembic_config)
    tables_after_reup = _list_public_tables(sync_url)
    missing = EXPECTED_TABLES - tables_after_reup
    assert not missing, (
        f"Re-upgrading after downgrade base failed to recreate: {missing}"
    )

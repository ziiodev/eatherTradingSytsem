"""Smoke test for :mod:`scripts.seed_dev`.

Runs the rich dev seed against the migrated **test** DB and asserts the
expected row counts land — closed/open orders, chat conversation, sleep
run, Q-Table, semantic rules, episodic rows — plus the idempotence
guarantee (a second invocation against the already-seeded DB inserts
zero new rows).

The test relies on the :func:`migrated_db` session fixture from
``tests/conftest.py``, plus the autouse ``_truncate_mutable_tables``
that wipes the per-test state — so by the time we start, the DB has the
schema at HEAD and is empty.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

# Make ``scripts.seed_dev`` importable. The script lives at
# ``apps/api/scripts/seed_dev.py`` which is NOT on the normal package
# path; we prepend the project root so ``import scripts.seed_dev`` works.
_API_ROOT = Path(__file__).resolve().parent.parent
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

pytestmark = pytest.mark.integration


async def test_seed_dev_populates_demo_data(app_client) -> None:
    """First invocation seeds everything; second is a strict no-op."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.chat_conversation import ChatConversation
    from aether_api.models.chat_message import ChatMessage
    from aether_api.models.episodic_memory import EpisodicMemory
    from aether_api.models.order import Order
    from aether_api.models.project import Project
    from aether_api.models.q_table import QTable
    from aether_api.models.semantic_memory import SemanticMemory
    from aether_api.models.sleep_report import SleepReport
    from aether_api.models.sleep_run import SleepRun
    from aether_api.models.user import User
    from scripts.seed_dev import (
        ALICE_EMAIL,
        BOB_EMAIL,
        CLOSED_ORDERS,
        DEMO_CHAT_TITLE,
        DEMO_PROJECT_NAME,
        EPISODIC_ROWS,
        OPEN_ORDERS,
        seed_dev,
    )

    maker = get_session_maker()

    # --- First run: full population.
    async with maker() as session:
        digest = await seed_dev(session)
        await session.commit()

    assert digest["alice_created"] is True
    assert digest["bob_created"] is True
    assert digest["project_created"] is True
    assert digest["project_status"] == "active"
    assert digest["orders_closed_inserted"] == CLOSED_ORDERS
    assert digest["orders_open_inserted"] == OPEN_ORDERS
    assert digest["chat_inserted"] is True
    assert digest["sleep_run_inserted"] is True
    assert digest["qtable_inserted"] is True
    assert digest["semantic_rules_inserted"] == 3
    assert digest["episodic_inserted"] == EPISODIC_ROWS

    # --- Verify DB-side row counts.
    async with maker() as session:
        users = (
            (
                await session.execute(
                    select(User.email).where(User.email.in_([ALICE_EMAIL, BOB_EMAIL]))
                )
            )
            .scalars()
            .all()
        )
        assert set(users) == {ALICE_EMAIL, BOB_EMAIL}

        project = (
            await session.execute(select(Project).where(Project.name == DEMO_PROJECT_NAME))
        ).scalar_one()
        assert project.status == "active"

        n_closed = await session.scalar(
            select(func.count(Order.id)).where(
                Order.project_id == project.id, Order.status == "closed"
            )
        )
        n_open = await session.scalar(
            select(func.count(Order.id)).where(
                Order.project_id == project.id, Order.status == "filled"
            )
        )
        assert n_closed == CLOSED_ORDERS
        assert n_open == OPEN_ORDERS

        n_convs = await session.scalar(
            select(func.count(ChatConversation.id)).where(
                ChatConversation.project_id == project.id,
                ChatConversation.title == DEMO_CHAT_TITLE,
            )
        )
        assert n_convs == 1

        n_msgs = await session.scalar(
            select(func.count(ChatMessage.id))
            .join(
                ChatConversation,
                ChatConversation.id == ChatMessage.conversation_id,
            )
            .where(ChatConversation.project_id == project.id)
        )
        assert n_msgs == 4

        n_sleep_runs = await session.scalar(
            select(func.count(SleepRun.id)).where(SleepRun.project_id == project.id)
        )
        assert n_sleep_runs == 1
        n_sleep_reports = await session.scalar(
            select(func.count(SleepReport.id))
            .join(SleepRun, SleepRun.id == SleepReport.sleep_run_id)
            .where(SleepRun.project_id == project.id)
        )
        assert n_sleep_reports == 1

        n_qtables = await session.scalar(
            select(func.count(QTable.id)).where(QTable.project_id == project.id)
        )
        assert n_qtables == 1

        n_semantic = await session.scalar(
            select(func.count(SemanticMemory.id)).where(
                SemanticMemory.project_id == project.id,
                SemanticMemory.active.is_(True),
            )
        )
        assert n_semantic == 3

        n_episodic = await session.scalar(
            select(func.count(EpisodicMemory.id)).where(EpisodicMemory.project_id == project.id)
        )
        assert n_episodic == EPISODIC_ROWS

    # --- Second run: idempotent — zero inserts.
    async with maker() as session:
        digest2 = await seed_dev(session)
        await session.commit()

    assert digest2["alice_created"] is False
    assert digest2["bob_created"] is False
    assert digest2["project_created"] is False
    assert digest2["orders_closed_inserted"] == 0
    assert digest2["orders_open_inserted"] == 0
    assert digest2["chat_inserted"] is False
    assert digest2["sleep_run_inserted"] is False
    assert digest2["qtable_inserted"] is False
    assert digest2["semantic_rules_inserted"] == 0
    assert digest2["episodic_inserted"] == 0

    # --- Total row counts unchanged.
    async with maker() as session:
        total_orders = await session.scalar(
            select(func.count(Order.id)).where(Order.project_id == project.id)
        )
        assert total_orders == CLOSED_ORDERS + OPEN_ORDERS
        total_episodic = await session.scalar(
            select(func.count(EpisodicMemory.id)).where(EpisodicMemory.project_id == project.id)
        )
        assert total_episodic == EPISODIC_ROWS

"""Approve / reject / revert lifecycle for config_versions."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


async def _seed_user_project(session):
    from tests._helpers import seed_project, seed_user

    user = await seed_user(
        session, email=f"applier-{uuid.uuid4().hex[:8]}@example.com",
        password="correct horse battery staple",
    )
    project = await seed_project(session, owner=user)
    project.risk_per_trade = Decimal("1.0")
    project.worker_params = {"sma_window": 30}
    project.notes = "baseline"
    await session.flush()
    await session.commit()
    return user, project


async def test_approve_applies_snapshot_to_project(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.pair_repository import PairRepository
    from aether_api.sleep.applier import apply_version
    from aether_api.sleep.repositories import ConfigVersionRepository

    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project(session)
        cv_repo = ConfigVersionRepository(session)
        pending = await cv_repo.create(
            project_id=project.id,
            snapshot={
                "risk_per_trade": "1.0",
                "worker_params": {"sma_window": 35},
                "notes": "post-sleep",
            },
            risk_class="medio",
            status="pending",
        )
        await session.commit()
        pending_id = pending.id

    async with maker() as session:
        applied = await apply_version(
            session, user_id=user.id, version_id=pending_id, decided_by=user.id
        )
        await session.commit()
        assert applied.status == "applied"
        assert applied.applied_at is not None
        assert applied.decided_by == user.id

    async with maker() as session:
        refreshed = await PairRepository(session).get_for_user(user.id, project.id)
        assert refreshed is not None
        assert refreshed.worker_params == {"sma_window": 35}
        assert refreshed.notes == "post-sleep"


async def test_reject_marks_rejected_without_touching_project(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.pair_repository import PairRepository
    from aether_api.sleep.applier import reject_version
    from aether_api.sleep.repositories import ConfigVersionRepository

    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project(session)
        original_notes = project.notes
        pending = await ConfigVersionRepository(session).create(
            project_id=project.id,
            snapshot={"notes": "never-applied"},
            risk_class="bajo",
            status="pending",
        )
        await session.commit()
        pending_id = pending.id

    async with maker() as session:
        rejected = await reject_version(
            session, user_id=user.id, version_id=pending_id, decided_by=user.id
        )
        await session.commit()
        assert rejected.status == "rejected"
        assert rejected.applied_at is None

    async with maker() as session:
        refreshed = await PairRepository(session).get_for_user(user.id, project.id)
        assert refreshed is not None
        assert refreshed.notes == original_notes


async def test_revert_appends_new_version_pointing_at_parent(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.pair_repository import PairRepository
    from aether_api.sleep.applier import apply_version, revert_version
    from aether_api.sleep.repositories import ConfigVersionRepository

    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project(session)
        cv_repo = ConfigVersionRepository(session)

        # Parent (the baseline) — mark as applied to qualify for revert
        # lineage.
        parent = await cv_repo.create(
            project_id=project.id,
            snapshot={
                "worker_params": {"sma_window": 30},
                "notes": "baseline",
            },
            risk_class="bajo",
            status="applied",
        )
        # Child pending — points at parent.
        child_pending = await cv_repo.create(
            project_id=project.id,
            snapshot={
                "worker_params": {"sma_window": 60},
                "notes": "after sleep",
            },
            risk_class="alto",
            status="pending",
            parent_version_id=parent.id,
        )
        await session.commit()
        parent_id = parent.id
        child_id = child_pending.id

    # Approve the child so we can then revert it.
    async with maker() as session:
        await apply_version(
            session, user_id=user.id, version_id=child_id, decided_by=user.id
        )
        await session.commit()

    async with maker() as session:
        reverted = await revert_version(
            session, user_id=user.id, version_id=child_id, decided_by=user.id
        )
        await session.commit()
        assert reverted.status == "applied"
        assert reverted.parent_version_id == child_id
        # Snapshot must mirror the parent's snapshot byte-for-byte.
        assert reverted.snapshot["worker_params"] == {"sma_window": 30}

    async with maker() as session:
        # The previously-applied child is now status='reverted'.
        cv_repo = ConfigVersionRepository(session)
        prior = await cv_repo.get(child_id)
        assert prior is not None
        assert prior.status == "reverted"

        # Project state mirrors the parent snapshot.
        refreshed = await PairRepository(session).get_for_user(user.id, project.id)
        assert refreshed is not None
        assert refreshed.worker_params == {"sma_window": 30}
        assert refreshed.notes == "baseline"

        # Lineage: project has parent → child (reverted) → revert (applied).
        all_for_project = await cv_repo.list_for_project(project.id, limit=10)
        statuses = {v.id: v.status for v in all_for_project}
        assert statuses[parent_id] == "applied"
        assert statuses[child_id] == "reverted"
        # The third entry is the appended revert row.
        revert_rows = [v for v in all_for_project if v.parent_version_id == child_id]
        assert len(revert_rows) == 1
        assert revert_rows[0].status == "applied"


async def test_approve_rejects_non_pending(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.applier import (
        ConfigVersionInvalidStateError,
        apply_version,
    )
    from aether_api.sleep.repositories import ConfigVersionRepository

    maker = get_session_maker()
    async with maker() as session:
        user, project = await _seed_user_project(session)
        cv_repo = ConfigVersionRepository(session)
        already_applied = await cv_repo.create(
            project_id=project.id,
            snapshot={"notes": "x"},
            risk_class="bajo",
            status="applied",
        )
        await session.commit()
        vid = already_applied.id

    async with maker() as session:
        with pytest.raises(ConfigVersionInvalidStateError):
            await apply_version(
                session, user_id=user.id, version_id=vid, decided_by=user.id
            )

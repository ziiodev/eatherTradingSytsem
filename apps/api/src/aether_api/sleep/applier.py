"""Apply / revert ``config_versions`` snapshots against a Project row.

Rules:

* ``apply`` writes the snapshot onto the project, marks the version
  ``status='applied'``, records ``applied_at``. Returns the refreshed
  ConfigVersion.
* ``revert`` is implemented as "append a NEW version pointing at the
  parent's snapshot" — we never mutate or delete a prior row. The new
  row inherits the parent's ``parent_version_id`` chain so lineage
  stays a DAG.

Tenant scoping: callers MUST already have verified the project belongs
to the user before calling these — the applier asserts the FK match as
a belt-and-suspenders check but does not load the user itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.config_version import ConfigVersion
from aether_api.repositories.pair_repository import PairRepository
from aether_api.sleep.repositories import ConfigVersionRepository
from aether_api.sleep.snapshot import apply_snapshot_to_project


class ConfigVersionNotFoundError(LookupError):
    """The requested config_version does not exist (or belongs to another tenant)."""


class ConfigVersionInvalidStateError(ValueError):
    """The version is not in a state that admits the requested transition."""


async def apply_version(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    version_id: uuid.UUID,
    decided_by: uuid.UUID,
) -> ConfigVersion:
    """Approve + apply the snapshot in ``version_id``.

    Pre-conditions:
    * version exists and is in status ``pending``.
    * underlying project belongs to ``user_id``.

    Side effects:
    * project columns updated to match the snapshot;
    * config_versions row moves ``pending → applied`` with
      ``decided_at`` + ``applied_at`` populated.
    """
    cv_repo = ConfigVersionRepository(session)
    proj_repo = PairRepository(session)

    version = await cv_repo.get(version_id)
    if version is None:
        raise ConfigVersionNotFoundError(str(version_id))

    project = await proj_repo.get_for_user(user_id, version.pair_id)
    if project is None:
        # Belongs to another tenant — preserve the 404-on-cross-tenant
        # contract by raising the same not-found error the router maps.
        raise ConfigVersionNotFoundError(str(version_id))

    if version.status != "pending":
        raise ConfigVersionInvalidStateError(
            f"cannot apply: current status={version.status!r}"
        )

    apply_snapshot_to_project(project, version.snapshot)
    await session.flush()

    now = datetime.now(tz=UTC).replace(tzinfo=None)
    updated = await cv_repo.update_status(
        version_id=version_id,
        status="applied",
        decided_by=decided_by,
        applied_at=now,
    )
    assert updated is not None  # we just refreshed it above
    return updated


async def reject_version(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    version_id: uuid.UUID,
    decided_by: uuid.UUID,
) -> ConfigVersion:
    """Mark a pending version ``rejected``. No project mutation."""
    cv_repo = ConfigVersionRepository(session)
    proj_repo = PairRepository(session)

    version = await cv_repo.get(version_id)
    if version is None:
        raise ConfigVersionNotFoundError(str(version_id))

    project = await proj_repo.get_for_user(user_id, version.pair_id)
    if project is None:
        raise ConfigVersionNotFoundError(str(version_id))

    if version.status != "pending":
        raise ConfigVersionInvalidStateError(
            f"cannot reject: current status={version.status!r}"
        )

    updated = await cv_repo.update_status(
        version_id=version_id,
        status="rejected",
        decided_by=decided_by,
    )
    assert updated is not None
    return updated


async def revert_version(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    version_id: uuid.UUID,
    decided_by: uuid.UUID,
) -> ConfigVersion:
    """Append a NEW version whose snapshot mirrors the parent's snapshot.

    Rules:
    * ``version_id`` MUST be currently ``applied``. Reverting an
      already-pending or rejected row makes no operational sense.
    * The new version is marked ``status='applied'`` immediately because
      the operator explicitly clicked "revert" — this is the OPPOSITE
      of the proposed-pending workflow and skips a second approval.
    * The reverted ``version_id`` itself moves to ``status='reverted'``
      so future queries surface the new lineage tip.
    """
    cv_repo = ConfigVersionRepository(session)
    proj_repo = PairRepository(session)

    version = await cv_repo.get(version_id)
    if version is None:
        raise ConfigVersionNotFoundError(str(version_id))

    project = await proj_repo.get_for_user(user_id, version.pair_id)
    if project is None:
        raise ConfigVersionNotFoundError(str(version_id))

    if version.status != "applied":
        raise ConfigVersionInvalidStateError(
            f"cannot revert: current status={version.status!r}; only 'applied' is revertible"
        )

    if version.parent_version_id is None:
        raise ConfigVersionInvalidStateError(
            "cannot revert: this is the first version in the project's lineage"
        )

    parent = await cv_repo.get(version.parent_version_id)
    if parent is None:
        raise ConfigVersionInvalidStateError(
            "cannot revert: parent_version_id no longer resolvable"
        )

    # Apply the parent's snapshot back onto the project.
    apply_snapshot_to_project(project, parent.snapshot)
    await session.flush()

    # Append the NEW version pointing back at the parent. risk_class is
    # inherited from the parent (revert is a known-good rollback, not
    # a new proposal).
    now = datetime.now(tz=UTC).replace(tzinfo=None)
    new_version = await cv_repo.create(
        project_id=version.pair_id,
        snapshot=parent.snapshot,
        risk_class=parent.risk_class,
        status="applied",
        sleep_run_id=None,
        parent_version_id=version.id,
    )
    # Stamp the applied / decided columns.
    stamped = await cv_repo.update_status(
        version_id=new_version.id,
        status="applied",
        decided_by=decided_by,
        applied_at=now,
    )
    assert stamped is not None

    # Move the just-reverted row to status='reverted'.
    await cv_repo.update_status(version_id=version.id, status="reverted")

    return stamped


__all__ = [
    "ConfigVersionInvalidStateError",
    "ConfigVersionNotFoundError",
    "apply_version",
    "reject_version",
    "revert_version",
]

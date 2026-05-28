"""``/api/projects/{id}/sleep/*`` and ``/api/config-versions/{id}/*``.

Endpoints:

* POST   /api/projects/{id}/sleep/trigger      — admin or project owner
* GET    /api/projects/{id}/sleep/runs         — list project runs
* GET    /api/projects/{id}/sleep/runs/{run_id}— detail with reflections
* POST   /api/config-versions/{id}/approve     — apply pending snapshot
* POST   /api/config-versions/{id}/reject      — mark rejected
* POST   /api/config-versions/{id}/revert      — re-apply parent snapshot

Tenant scoping is enforced at every endpoint via the underlying
repositories' ``user_id`` filters; cross-tenant access returns 404
(never 403). All state-changing endpoints require CSRF.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.sleep.applier import (
    ConfigVersionInvalidStateError,
    ConfigVersionNotFoundError,
    apply_version,
    reject_version,
    revert_version,
)
from aether_api.sleep.orchestrator import PHASE_TYPES, run_sleep_phase
from aether_api.sleep.repositories import (
    ConfigVersionRepository,
    SleepReflectionRepository,
    SleepRunRepository,
)
from aether_api.tenancy.middleware import csrf_dependency, current_user

projects_sleep_router = APIRouter(
    prefix="/api/projects", tags=["sleep-phase"]
)
config_versions_router = APIRouter(
    prefix="/api/config-versions", tags=["sleep-phase"]
)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
class TriggerSleepBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase_type: str = Field(..., description="One of: micro | profundo | critico")


class SleepRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    phase_type: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str | None = None
    error: str | None = None


class SleepReflectionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_type: str
    reflection_md: str | None = None
    suggested_changes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class ConfigVersionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    parent_version_id: uuid.UUID | None = None
    sleep_run_id: uuid.UUID | None = None
    snapshot: dict[str, Any]
    risk_class: str
    status: str
    proposed_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: uuid.UUID | None = None
    applied_at: datetime | None = None


class SleepRunDetailResponse(BaseModel):
    run: SleepRunSummary
    reflections: list[SleepReflectionDetail]
    config_versions: list[ConfigVersionDetail]


class SleepRunListResponse(BaseModel):
    items: list[SleepRunSummary]
    total: int
    limit: int
    offset: int


class TriggerSleepResponse(BaseModel):
    sleep_run_id: uuid.UUID
    status: str
    summary: str | None = None
    error: str | None = None
    config_version_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# /api/projects/{id}/sleep/...
# ---------------------------------------------------------------------------
@projects_sleep_router.post(
    "/{project_id}/sleep/trigger",
    response_model=TriggerSleepResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def trigger_sleep_run(
    project_id: uuid.UUID,
    body: TriggerSleepBody,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TriggerSleepResponse:
    """Manually fire one Micro / Profundo / Crítico sleep run.

    Authorisation: admin OR project owner. Cross-tenant returns 404
    (matches the rest of /api/projects).
    """
    if body.phase_type not in PHASE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown phase_type {body.phase_type!r}",
        )

    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    # Admin escape hatch: an admin can trigger on any project, but the
    # run is attributed to the project's owner (we look up the row
    # without the tenant filter).
    if project is None:
        from sqlalchemy import select

        from aether_api.models.project import Project

        select_result = await session.execute(
            select(Project).where(Project.id == project_id)
        )
        project = select_result.scalar_one_or_none()
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
            )

    orchestrator_result = await run_sleep_phase(
        session,
        project_id=project.id,
        user_id=project.user_id,
        phase_type=body.phase_type,
    )
    return TriggerSleepResponse(
        sleep_run_id=orchestrator_result.sleep_run_id,
        status=orchestrator_result.status,
        summary=orchestrator_result.summary,
        error=orchestrator_result.error,
        config_version_id=orchestrator_result.config_version_id,
    )


@projects_sleep_router.get(
    "/{project_id}/sleep/runs",
    response_model=SleepRunListResponse,
)
async def list_sleep_runs(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SleepRunListResponse:
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )
    run_repo = SleepRunRepository(session)
    rows = await run_repo.list_for_project(
        project_id, limit=limit, offset=offset
    )
    return SleepRunListResponse(
        items=[SleepRunSummary.model_validate(row) for row in rows],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


@projects_sleep_router.get(
    "/{project_id}/sleep/runs/{run_id}",
    response_model=SleepRunDetailResponse,
)
async def get_sleep_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SleepRunDetailResponse:
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    run_repo = SleepRunRepository(session)
    refl_repo = SleepReflectionRepository(session)
    cv_repo = ConfigVersionRepository(session)

    run = await run_repo.get(run_id)
    if run is None or run.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="sleep run not found"
        )

    reflections = await refl_repo.list_for_run(run_id)
    config_versions = await cv_repo.list_for_run(run_id)

    return SleepRunDetailResponse(
        run=SleepRunSummary.model_validate(run),
        reflections=[
            SleepReflectionDetail.model_validate(r) for r in reflections
        ],
        config_versions=[
            ConfigVersionDetail.model_validate(v) for v in config_versions
        ],
    )


# ---------------------------------------------------------------------------
# /api/config-versions/{id}/...
# ---------------------------------------------------------------------------
def _maps_applier_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConfigVersionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="config version not found"
        )
    if isinstance(exc, ConfigVersionInvalidStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"unexpected applier error: {exc!r}",
    )


@config_versions_router.post(
    "/{version_id}/approve",
    response_model=ConfigVersionDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def approve_config_version(
    version_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionDetail:
    try:
        version = await apply_version(
            session,
            user_id=user.id,
            version_id=version_id,
            decided_by=user.id,
        )
    except (ConfigVersionNotFoundError, ConfigVersionInvalidStateError) as exc:
        raise _maps_applier_error(exc) from exc
    await session.commit()
    return ConfigVersionDetail.model_validate(version)


@config_versions_router.post(
    "/{version_id}/reject",
    response_model=ConfigVersionDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def reject_config_version(
    version_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionDetail:
    try:
        version = await reject_version(
            session,
            user_id=user.id,
            version_id=version_id,
            decided_by=user.id,
        )
    except (ConfigVersionNotFoundError, ConfigVersionInvalidStateError) as exc:
        raise _maps_applier_error(exc) from exc
    await session.commit()
    return ConfigVersionDetail.model_validate(version)


@config_versions_router.post(
    "/{version_id}/revert",
    response_model=ConfigVersionDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def revert_config_version(
    version_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfigVersionDetail:
    # Honour the revert window — refuse to revert a snapshot older than
    # the configured horizon. This is the operator-policy guardrail
    # against rolling back stale state without a follow-up review.
    settings = get_settings()
    cv_repo = ConfigVersionRepository(session)
    existing = await cv_repo.get(version_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="config version not found"
        )
    if existing.applied_at is not None:
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        cutoff = _dt.now(tz=UTC).replace(tzinfo=None) - _td(
            hours=settings.sleep_revert_window_hours
        )
        if existing.applied_at < cutoff:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "revert window expired: "
                    f"applied_at older than {settings.sleep_revert_window_hours}h"
                ),
            )

    try:
        version = await revert_version(
            session,
            user_id=user.id,
            version_id=version_id,
            decided_by=user.id,
        )
    except (ConfigVersionNotFoundError, ConfigVersionInvalidStateError) as exc:
        raise _maps_applier_error(exc) from exc
    await session.commit()
    return ConfigVersionDetail.model_validate(version)


__all__ = [
    "config_versions_router",
    "projects_sleep_router",
]

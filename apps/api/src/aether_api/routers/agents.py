"""``/api/agents`` — tenant-scoped CRUD for agents.

Scope: agent *definitions*. Execution of ``logica`` is NOT in this
surface — it lands in the separate ``agent-execution-sandbox`` change.
We store the source text, validate it parses as Python, surface
non-blocking convention warnings, and that is all.

Endpoints:

* ``GET    /api/agents``                — list (filter by ``type`` / ``is_active``).
* ``GET    /api/agents/{id}``           — detail (includes ``logica``).
* ``POST   /api/agents``                — create.
* ``PATCH  /api/agents/{id}``           — partial update (optimistic locking).
* ``POST   /api/agents/{id}/archive``   — soft-archive (idempotent).
* ``DELETE /api/agents/{id}``           — hard delete (409 if referenced).

Tenancy: every write resolves ``user_id`` from the authenticated session
and IGNORES any ``user_id`` field in the body. Cross-tenant reads return
404, never 403 (existence is not disclosed).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session
from aether_api.models.agent import AGENT_TYPES, Agent
from aether_api.models.user import User
from aether_api.repositories.agent_repository import (
    AgentReferencedError,
    AgentRepository,
)
from aether_api.repositories.agent_runs_repository import AgentRunsRepository
from aether_api.repositories.agent_skill_repository import (
    AgentSkillAlreadyAttachedError,
    AgentSkillRepository,
    AgentSkillTenancyError,
)
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.tenancy.middleware import (
    admin_required,
    csrf_dependency,
    current_user,
)
from aether_api.validation.logica import (
    ENTRYPOINT_REGEX,
    LogicaParseTimeoutError,
    LogicaSyntaxError,
    LogicaTooLargeError,
    validate_logica_shape,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

AgentType = Literal["orchestrator", "worker", "investigator", "auditor"]

#: Default entrypoint name per agent type. The UI seeds the template
#: with a ``def`` of the same name; if the operator renames the function
#: without renaming ``entrypoint``, we surface a non-blocking warning.
#:
#: Charter correction (migration 0010): the Orquestador joins the map
#: with ``orchestrate(ctx)`` as the canonical entrypoint.
_DEFAULT_ENTRYPOINTS: dict[str, str] = {
    "orchestrator": "orchestrate",
    "worker": "on_tick",
    "investigator": "investigate",
    "auditor": "audit",
}


class AgentCreateRequest(BaseModel):
    """Create payload. ``user_id`` is server-derived, never accepted."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=100)]
    type: AgentType
    logica: Annotated[str, Field(min_length=1)]
    description: Annotated[str | None, Field(default=None, max_length=4000)]
    entrypoint: Annotated[
        str | None,
        Field(default=None, pattern=ENTRYPOINT_REGEX, max_length=120),
    ]

    @field_validator("name", "description", "entrypoint")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        # ``Field(min_length=1)`` runs BEFORE this — strip only after,
        # to surface "whitespace-only" as a 422.
        if v is None:
            return None
        stripped = v.strip()
        return stripped


class AgentPatchRequest(BaseModel):
    """Partial update. All fields optional; merged onto the loaded row.

    Optimistic-locking precondition: ``updated_at`` MUST match the value
    the client most recently fetched. If it does not, the server returns
    409 Conflict so the UI can prompt the operator to reload.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=100)]
    description: Annotated[str | None, Field(default=None, max_length=4000)]
    logica: Annotated[str | None, Field(default=None, min_length=1)]
    entrypoint: Annotated[
        str | None,
        Field(default=None, pattern=ENTRYPOINT_REGEX, max_length=120),
    ]
    type: AgentType | None = None
    #: Server-supplied timestamp from the last GET. Required for
    #: optimistic locking — if missing, the server rejects the PATCH
    #: with 428 Precondition Required.
    updated_at: datetime | None = None


class AgentSummary(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    is_active: bool
    version: int
    updated_at: datetime | None
    projects_using: int = 0


class AgentDetail(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    is_active: bool
    version: int
    description: str | None
    entrypoint: str | None
    logica: str
    created_at: datetime | None
    updated_at: datetime | None
    warnings: list[str] = []


class AgentArchiveResponse(BaseModel):
    id: uuid.UUID
    is_active: bool
    version: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entrypoint_warnings(
    *, type_: str, entrypoint: str | None, logica: str
) -> list[str]:
    """Return human-readable, non-blocking warnings about the entrypoint.

    Checks:

    * If ``entrypoint`` is None, suggest the canonical name for the type.
    * If ``entrypoint`` is set but the source does not contain a
      ``def {entrypoint}(`` at column 0, warn that the sandbox will fail
      to import it.

    These are surfaced in the response body so the editor can render
    them inline; they NEVER block the request.
    """
    warnings: list[str] = []
    canonical = _DEFAULT_ENTRYPOINTS.get(type_)
    if entrypoint is None:
        if canonical:
            warnings.append(
                f"entrypoint is unset; convention for '{type_}' agents is '{canonical}'"
            )
        return warnings

    # Cheap substring check — we already parsed the AST in the caller,
    # but doing AST-walking here doubles the work. A leading-line / def
    # marker is good enough for a non-blocking warning.
    needle = f"def {entrypoint}("
    if needle not in logica:
        warnings.append(
            f"entrypoint '{entrypoint}' is declared but no top-level "
            f"`def {entrypoint}(` was found in logica"
        )
    elif canonical and entrypoint != canonical:
        # Allowed — just informational.
        warnings.append(
            f"entrypoint '{entrypoint}' differs from convention '{canonical}' "
            f"for '{type_}' agents (allowed, just heads-up)"
        )
    return warnings


async def _validate_or_422(source: str) -> None:
    """Run :func:`validate_logica_shape` and translate to HTTPException."""
    try:
        await validate_logica_shape(source)
    except LogicaTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "logica_too_large",
                "size_bytes": exc.size_bytes,
                "message": str(exc),
            },
        ) from exc
    except LogicaSyntaxError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "logica_syntax_error",
                "line": exc.line,
                "col": exc.col,
                "message": exc.message,
            },
        ) from exc
    except LogicaParseTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "logica_parse_timeout",
                "message": "logica failed to parse within the time budget",
            },
        ) from exc


def _agent_to_detail(agent: Agent, *, warnings: list[str] | None = None) -> AgentDetail:
    return AgentDetail(
        id=agent.id,
        name=agent.name,
        type=agent.type,
        is_active=agent.is_active,
        version=agent.version,
        description=agent.description,
        entrypoint=agent.entrypoint,
        logica=agent.logica,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        warnings=warnings or [],
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    type_filter: Annotated[str | None, Query(alias="type", max_length=20)] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> list[AgentSummary]:
    if type_filter is not None and type_filter not in AGENT_TYPES:
        raise HTTPException(status_code=400, detail="invalid agent type")
    repo = AgentRepository(session)
    rows = await repo.list_for_user(user.id, type=type_filter, is_active=is_active)
    counts = await repo.projects_using_counts(user.id, [row.id for row in rows])
    return [
        AgentSummary(
            id=row.id,
            name=row.name,
            type=row.type,
            is_active=row.is_active,
            version=row.version,
            updated_at=row.updated_at,
            projects_using=counts.get(row.id, 0),
        )
        for row in rows
    ]


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(
    agent_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentDetail:
    repo = AgentRepository(session)
    row = await repo.get_for_user(user.id, agent_id)
    if row is None:
        # Cross-tenant denial → 404, NOT 403.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    return _agent_to_detail(row)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=AgentDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_agent(
    payload: AgentCreateRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentDetail:
    await _validate_or_422(payload.logica)
    repo = AgentRepository(session)
    agent = await repo.create(
        user_id=user.id,
        name=payload.name,
        type=payload.type,
        logica=payload.logica,
        description=payload.description,
        entrypoint=payload.entrypoint,
    )
    await session.commit()
    warnings = _entrypoint_warnings(
        type_=agent.type, entrypoint=agent.entrypoint, logica=agent.logica
    )
    return _agent_to_detail(agent, warnings=warnings)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@router.patch(
    "/{agent_id}",
    response_model=AgentDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_agent(
    agent_id: uuid.UUID,
    payload: AgentPatchRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentDetail:
    if payload.updated_at is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="updated_at precondition required for PATCH",
        )

    repo = AgentRepository(session)
    agent = await repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    # Optimistic locking — reject the PATCH if the row has been written
    # to since the client last read it. Comparing on the second to absorb
    # asyncpg/psycopg timestamp precision differences.
    current_ts = agent.updated_at
    client_ts = payload.updated_at
    if (
        current_ts is not None
        and client_ts is not None
        and int(current_ts.timestamp()) != int(client_ts.timestamp())
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "stale_update",
                "message": "agent has been modified since you loaded it; reload and retry",
                "server_updated_at": current_ts.isoformat(),
            },
        )

    changes: dict[str, Any] = {}
    if payload.name is not None and payload.name != agent.name:
        changes["name"] = payload.name
    if payload.description is not None and payload.description != agent.description:
        changes["description"] = payload.description
    if payload.entrypoint is not None and payload.entrypoint != agent.entrypoint:
        changes["entrypoint"] = payload.entrypoint
    if payload.type is not None and payload.type != agent.type:
        changes["type"] = payload.type

    bump_version = False
    if payload.logica is not None and payload.logica != agent.logica:
        await _validate_or_422(payload.logica)
        changes["logica"] = payload.logica
        bump_version = True

    if not changes:
        # Nothing to do — return current state without writing.
        warnings = _entrypoint_warnings(
            type_=agent.type, entrypoint=agent.entrypoint, logica=agent.logica
        )
        return _agent_to_detail(agent, warnings=warnings)

    await repo.patch(agent, changes=changes, bump_version=bump_version)
    await session.commit()

    warnings = _entrypoint_warnings(
        type_=agent.type, entrypoint=agent.entrypoint, logica=agent.logica
    )
    return _agent_to_detail(agent, warnings=warnings)


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


@router.post(
    "/{agent_id}/archive",
    response_model=AgentArchiveResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def archive_agent(
    agent_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentArchiveResponse:
    repo = AgentRepository(session)
    agent = await repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    await repo.archive(agent)
    await session.commit()
    return AgentArchiveResponse(id=agent.id, is_active=agent.is_active, version=agent.version)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def delete_agent(
    agent_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    repo = AgentRepository(session)
    agent = await repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    try:
        await repo.delete(user.id, agent)
    except AgentReferencedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "agent_referenced",
                "message": (
                    f"agent is referenced by {len(exc.project_ids)} project(s); "
                    "unlink them before deleting"
                ),
                "project_ids": [str(pid) for pid in exc.project_ids],
            },
        ) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Sandbox execution — see ``sdd/agent-execution-sandbox/{spec,design}``.
#
# These endpoints live on the agents router (not a new ``sandbox`` router) so
# the URL shape stays nested under the parent resource. The actual code path
# always funnels through ``aether_api.sandbox.engine.Engine.run_agent``.
# ---------------------------------------------------------------------------


class AgentRunRequest(BaseModel):
    """Body of ``POST /api/agents/{id}/run``.

    ``inputs`` is the free-form JSON-ish payload forwarded into
    :attr:`AgentContext.inputs`. We forbid extra fields so a client
    typo (``project`` vs ``project_id``) is a 422 not a silent no-op.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    dry_run: bool = True  # default-safe — non-dry-run is opt-in.
    inputs: dict[str, Any] = Field(default_factory=dict)


class AgentRunSummary(BaseModel):
    """Subset of the ``agent_runs`` row echoed back to the caller."""

    id: uuid.UUID
    agent_id: uuid.UUID
    project_id: uuid.UUID
    status: str
    started_at: datetime
    ended_at: datetime | None
    exit_code: int | None
    denial_reason: str | None
    duration_seconds: float | None = None


class AgentRunDetail(AgentRunSummary):
    """Full row with captured streams + structured resource accounting."""

    stdout: str | None
    stderr: str | None
    resource_usage: dict[str, Any]
    result: Any = None


@router.post(
    "/{agent_id}/run",
    response_model=AgentRunDetail,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(csrf_dependency), Depends(admin_required)],
)
async def run_agent(
    agent_id: uuid.UUID,
    payload: AgentRunRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgentRunDetail:
    """Execute ``agents.logica`` inside the sandbox.

    Cross-tenant model: the agent AND project MUST both belong to
    ``current_user`` (and to each other). Any mismatch returns 404 — the
    non-disclosure contract from ``specs/multi-tenancy`` applies to both
    referents, NOT just the URL path parameter.

    Feature-flag: returns 503 with ``{detail: "sandbox not enabled"}``
    when ``settings.agent_sandbox_enabled`` is False. Admin-only;
    non-admins get 403 from the ``admin_required`` dependency.
    """
    settings = get_settings()
    if not settings.agent_sandbox_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox not enabled",
        )

    agent_repo = AgentRepository(session)
    project_repo = ProjectRepository(session)
    runs_repo = AgentRunsRepository(session)

    # Load BOTH referents under the tenant filter. The 404 path is
    # identical whether the row is missing or owned by someone else —
    # cross-tenant existence is never disclosed.
    agent = await agent_repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    project = await project_repo.get_for_user(user.id, payload.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    # Tenant integrity is double-checked (defence in depth) — the repos
    # already filtered, but a future change that swaps in a non-tenant
    # repo MUST still trip here.
    if agent.user_id != user.id or project.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    # Insert audit row BEFORE spawning the child so a crash leaves a
    # discoverable "running" row in the table.
    run_row = await runs_repo.record_start(
        user_id=user.id,
        agent_id=agent.id,
        project_id=project.id,
    )
    await session.commit()

    # Build engine from settings every call — Settings is cached, this
    # is cheap, and it lets ops dial rlimits via env without restart.
    from aether_api.sandbox.engine import Engine

    engine = Engine(
        wall_clock_seconds=settings.agent_sandbox_wall_clock_seconds,
        rlimit_cpu_seconds=settings.agent_sandbox_rlimit_cpu_seconds,
        rlimit_as_bytes=settings.agent_sandbox_rlimit_as_bytes,
        rlimit_nofile=settings.agent_sandbox_rlimit_nofile,
        rlimit_fsize=settings.agent_sandbox_rlimit_fsize_bytes,
    )

    # Engine spawn is blocking — run it on a worker thread so the event
    # loop isn't pinned while the child does its thing.
    import anyio

    result = await anyio.to_thread.run_sync(
        lambda: engine.run_agent(
            agent_row=agent,
            project_row=project,
            inputs=payload.inputs,
            dry_run=payload.dry_run,
            mode="manual",
            run_id=run_row.id,
        )
    )

    await runs_repo.record_finish(
        run_row.id,
        status=result.status,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        denial_reason=result.denial_reason,
        resource_usage=result.resource_usage,
    )
    await session.commit()

    # Re-load so we return the final shape from the DB (started_at /
    # ended_at landed there via NOW() + record_finish).
    final = await runs_repo.get_for_user(user.id, run_row.id)
    assert final is not None  # we just wrote it; the DB cannot have lost it.

    return AgentRunDetail(
        id=final.id,
        agent_id=final.agent_id,
        project_id=final.project_id,
        status=final.status,
        started_at=final.started_at,
        ended_at=final.ended_at,
        exit_code=final.exit_code,
        denial_reason=final.denial_reason,
        duration_seconds=result.duration_seconds,
        stdout=final.stdout,
        stderr=final.stderr,
        resource_usage=final.resource_usage,
        result=result.result,
    )


@router.get("/{agent_id}/runs", response_model=list[AgentRunSummary])
async def list_agent_runs(
    agent_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AgentRunSummary]:
    """Return the caller's recent runs of ``agent_id``, newest first.

    Cross-tenant agent_id returns 404 — same non-disclosure contract as
    the rest of the resource. Listing is open to any authenticated owner
    (NOT just admins) so non-admin operators can still see their own
    history; the *execute* surface is admin-only.
    """
    agent_repo = AgentRepository(session)
    runs_repo = AgentRunsRepository(session)

    agent = await agent_repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await runs_repo.list_for_agent(user.id, agent_id, limit=limit)
    return [
        AgentRunSummary(
            id=row.id,
            agent_id=row.agent_id,
            project_id=row.project_id,
            status=row.status,
            started_at=row.started_at,
            ended_at=row.ended_at,
            exit_code=row.exit_code,
            denial_reason=row.denial_reason,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Agent ↔ Skill bindings
# ---------------------------------------------------------------------------
#
# A skill is "attached" to an agent by a row in ``agent_skills`` (see
# migration ``0009_skills_markdown_and_agent_skills``). Both endpoints are
# tenant-scoped via the agent (and the skill, on attach). Cross-tenant
# access — either by URL path or by skill_id in the body — collapses to
# a 404, NEVER 403, per the project-wide non-disclosure contract.


class AttachedSkill(BaseModel):
    """Row returned by ``GET /api/agents/{id}/skills``.

    Carries both the binding (id, created_at, notes) and the skill
    summary so the UI doesn't need a follow-up roundtrip per row.
    """

    model_config = ConfigDict(extra="forbid")

    binding_id: uuid.UUID
    skill_id: uuid.UUID
    name: str
    type: str
    runtime: str
    is_active: bool
    version: int
    notes: str | None
    created_at: datetime


class AttachSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: uuid.UUID
    notes: Annotated[str | None, Field(default=None, max_length=4000)]


@router.get("/{agent_id}/skills", response_model=list[AttachedSkill])
async def list_agent_skills(
    agent_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AttachedSkill]:
    """List skills attached to ``agent_id`` (newest binding first).

    Cross-tenant agent_id returns 404 — non-disclosure applies.
    """
    agent_repo = AgentRepository(session)
    agent = await agent_repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    binding_repo = AgentSkillRepository(session)
    pairs = await binding_repo.list_for_agent(user.id, agent_id)
    return [
        AttachedSkill(
            binding_id=binding.id,
            skill_id=skill.id,
            name=skill.name,
            type=skill.type,
            runtime=skill.runtime,
            is_active=skill.is_active,
            version=skill.version,
            notes=binding.notes,
            created_at=binding.created_at,
        )
        for binding, skill in pairs
    ]


@router.post(
    "/{agent_id}/skills",
    response_model=AttachedSkill,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def attach_skill_to_agent(
    agent_id: uuid.UUID,
    payload: AttachSkillRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AttachedSkill:
    """Attach ``payload.skill_id`` to ``agent_id``.

    * Either endpoint not owned by ``current_user`` → 404.
    * Already attached → 409 with ``code = "skill_already_attached"``.
    """
    binding_repo = AgentSkillRepository(session)
    try:
        binding = await binding_repo.attach(
            user_id=user.id,
            agent_id=agent_id,
            skill_id=payload.skill_id,
            notes=payload.notes,
        )
    except AgentSkillTenancyError as exc:
        # Single 404 for both "agent missing" and "skill missing/foreign" —
        # cross-tenant existence is never disclosed.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        ) from exc
    except AgentSkillAlreadyAttachedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "skill_already_attached",
                "message": (
                    f"skill {payload.skill_id} is already attached to "
                    f"agent {agent_id}"
                ),
            },
        ) from exc
    await session.commit()

    # Hydrate the response with the skill row so the UI can render it
    # without a follow-up GET.
    pairs = await binding_repo.list_for_agent(user.id, agent_id)
    fresh = next((p for p in pairs if p[0].id == binding.id), None)
    if fresh is None:
        # Shouldn't happen — we just inserted it under the same tenant.
        raise HTTPException(  # pragma: no cover
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="binding created but not visible to caller",
        )
    binding_row, skill_row = fresh
    return AttachedSkill(
        binding_id=binding_row.id,
        skill_id=skill_row.id,
        name=skill_row.name,
        type=skill_row.type,
        runtime=skill_row.runtime,
        is_active=skill_row.is_active,
        version=skill_row.version,
        notes=binding_row.notes,
        created_at=binding_row.created_at,
    )


@router.delete(
    "/{agent_id}/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def detach_skill_from_agent(
    agent_id: uuid.UUID,
    skill_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Detach ``skill_id`` from ``agent_id``.

    * Either endpoint not owned by ``current_user`` → 404.
    * Binding does not exist → 404 (same non-disclosure contract).
    """
    agent_repo = AgentRepository(session)
    agent = await agent_repo.get_for_user(user.id, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    binding_repo = AgentSkillRepository(session)
    affected = await binding_repo.detach(
        user_id=user.id, agent_id=agent_id, skill_id=skill_id
    )
    if affected == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="binding not found",
        )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

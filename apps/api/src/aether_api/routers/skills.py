"""``/api/skills`` — tenant-scoped CRUD for the v1 skills catalog.

Scope: skill *definitions*. Execution of ``code`` is NOT in this surface
— it lands in the future ``agent-execution-sandbox`` change. We store
the source text, validate it parses as Python (``ast.parse`` off-thread
with a hard timeout), and that is all.

Endpoints:

* ``GET    /api/skills``              — list (filter by ``type`` / ``is_active``).
* ``GET    /api/skills/{id}``         — detail (includes ``code`` + signatures).
* ``POST   /api/skills``              — create.
* ``PATCH  /api/skills/{id}``         — partial update (optimistic locking).
* ``POST   /api/skills/{id}/archive`` — soft-archive (idempotent).
* ``DELETE /api/skills/{id}``         — hard delete.

Tenancy: every write resolves ``user_id`` from the authenticated session
and IGNORES any ``user_id`` field in the body. Cross-tenant reads return
404, never 403 (existence is not disclosed). State-changing endpoints
are CSRF-gated via the shared double-submit dependency.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.skill import SKILL_TYPES, SkillDefinition
from aether_api.models.user import User
from aether_api.repositories.agent_skill_repository import AgentSkillRepository
from aether_api.repositories.skill_repository import (
    SkillReferencedError,
    SkillRepository,
)
from aether_api.tenancy.middleware import csrf_dependency, current_user
from aether_api.validation.logica import (
    MAX_LOGICA_BYTES,
    LogicaParseTimeoutError,
    LogicaSyntaxError,
    LogicaTooLargeError,
    validate_logica_shape,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SkillType = Literal["indicator", "data_source", "analytic", "executor", "risk"]
SkillRuntime = Literal["markdown", "python"]


class SignatureField(BaseModel):
    """One named slot in the skill input/output signature.

    The v1 ``type`` is a free-form string — there is no validation of
    whether ``"int"`` is a real Python type, etc. The sandbox change is
    where the type system gets locked down; for now we just round-trip
    the string so the UI can display it.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    type: Annotated[str, Field(min_length=1, max_length=80)]


class SkillSignature(BaseModel):
    """Slim TypedDict-like signature persisted as JSONB.

    Note this is NOT a JSON Schema — see ``sdd/skills-catalog/spec``.
    """

    model_config = ConfigDict(extra="forbid")

    inputs: list[SignatureField] = Field(default_factory=list)
    outputs: list[SignatureField] = Field(default_factory=list)


class SkillCreateRequest(BaseModel):
    """Create payload. ``user_id`` is server-derived, never accepted.

    ``runtime`` defaults to ``'markdown'`` per the charter correction —
    skills are knowledge artifacts by default; Python is reserved for
    computational/algorithmic capabilities.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=100)]
    type: SkillType
    code: Annotated[str, Field(min_length=1)]
    runtime: SkillRuntime = "markdown"
    description: Annotated[str | None, Field(default=None, max_length=4000)]
    input_signature: SkillSignature = Field(default_factory=SkillSignature)
    output_signature: SkillSignature = Field(default_factory=SkillSignature)

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()


class SkillPatchRequest(BaseModel):
    """Partial update. All fields optional; merged onto the loaded row.

    Optimistic-locking precondition: ``updated_at`` MUST match the value
    the client most recently fetched. If it does not, the server returns
    409 Conflict so the UI can prompt the operator to reload.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=100)]
    description: Annotated[str | None, Field(default=None, max_length=4000)]
    code: Annotated[str | None, Field(default=None, min_length=1)]
    type: SkillType | None = None
    runtime: SkillRuntime | None = None
    input_signature: SkillSignature | None = None
    output_signature: SkillSignature | None = None
    #: Server-supplied timestamp from the last GET. Required for
    #: optimistic locking — if missing, the server rejects the PATCH
    #: with 428 Precondition Required.
    updated_at: datetime | None = None


class SkillSummary(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    runtime: str
    is_active: bool
    version: int
    updated_at: datetime | None
    used_by_agent_count: int = 0


class SkillDetail(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    runtime: str
    is_active: bool
    version: int
    description: str | None
    code: str
    input_signature: dict[str, Any]
    output_signature: dict[str, Any]
    created_at: datetime | None
    updated_at: datetime | None
    used_by_agent_count: int = 0


class SkillArchiveResponse(BaseModel):
    id: uuid.UUID
    is_active: bool
    version: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _validate_or_422(source: str, *, runtime: str) -> None:
    """Validate the skill ``code`` body for the given ``runtime``.

    * ``runtime == 'python'`` reuses the agents-style off-thread
      ``ast.parse`` with a hard timeout — same primitive as
      :mod:`aether_api.validation.logica`. Errors surface as
      ``python_*`` (renamed from ``code_*`` for clarity now that markdown
      is a peer runtime).
    * ``runtime == 'markdown'`` is permissive: only a size cap applies.
      Markdown is freeform prose; any non-empty content is accepted.
    """
    if runtime == "python":
        try:
            await validate_logica_shape(source)
        except LogicaTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "python_too_large",
                    "size_bytes": exc.size_bytes,
                    "message": str(exc),
                },
            ) from exc
        except LogicaSyntaxError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "python_syntax_error",
                    "line": exc.line,
                    "col": exc.col,
                    "message": exc.message,
                },
            ) from exc
        except LogicaParseTimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "python_parse_timeout",
                    "message": "code failed to parse within the time budget",
                },
            ) from exc
        return

    # ``runtime == 'markdown'`` — size cap only, no parser.
    size_bytes = len(source.encode("utf-8"))
    if size_bytes > MAX_LOGICA_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "markdown_too_large",
                "size_bytes": size_bytes,
                "message": (
                    f"markdown too large: {size_bytes} bytes "
                    f"(max {MAX_LOGICA_BYTES})"
                ),
            },
        )


def _skill_to_detail(
    skill: SkillDefinition, *, used_by_agent_count: int = 0
) -> SkillDetail:
    return SkillDetail(
        id=skill.id,
        name=skill.name,
        type=skill.type,
        runtime=skill.runtime,
        is_active=skill.is_active,
        version=skill.version,
        description=skill.description,
        code=skill.code,
        input_signature=skill.input_signature or {},
        output_signature=skill.output_signature or {},
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        used_by_agent_count=used_by_agent_count,
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SkillSummary])
async def list_skills(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    type_filter: Annotated[str | None, Query(alias="type", max_length=20)] = None,
    is_active: Annotated[bool | None, Query()] = None,
) -> list[SkillSummary]:
    if type_filter is not None and type_filter not in SKILL_TYPES:
        raise HTTPException(status_code=400, detail="invalid skill type")
    repo = SkillRepository(session)
    binding_repo = AgentSkillRepository(session)
    rows = await repo.list_for_user(user.id, type=type_filter, is_active=is_active)
    summaries: list[SkillSummary] = []
    for row in rows:
        count = await binding_repo.count_for_skill(user.id, row.id)
        summaries.append(
            SkillSummary(
                id=row.id,
                name=row.name,
                type=row.type,
                runtime=row.runtime,
                is_active=row.is_active,
                version=row.version,
                updated_at=row.updated_at,
                used_by_agent_count=count,
            )
        )
    return summaries


@router.get("/{skill_id}", response_model=SkillDetail)
async def get_skill(
    skill_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillDetail:
    repo = SkillRepository(session)
    binding_repo = AgentSkillRepository(session)
    row = await repo.get_for_user(user.id, skill_id)
    if row is None:
        # Cross-tenant denial → 404, NOT 403.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    count = await binding_repo.count_for_skill(user.id, row.id)
    return _skill_to_detail(row, used_by_agent_count=count)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_skill(
    payload: SkillCreateRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillDetail:
    await _validate_or_422(payload.code, runtime=payload.runtime)
    repo = SkillRepository(session)
    skill = await repo.create(
        user_id=user.id,
        name=payload.name,
        type=payload.type,
        code=payload.code,
        runtime=payload.runtime,
        description=payload.description,
        input_signature=payload.input_signature.model_dump(),
        output_signature=payload.output_signature.model_dump(),
    )
    await session.commit()
    # Brand-new skill — no agents could have attached to it yet.
    return _skill_to_detail(skill, used_by_agent_count=0)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@router.patch(
    "/{skill_id}",
    response_model=SkillDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_skill(
    skill_id: uuid.UUID,
    payload: SkillPatchRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillDetail:
    if payload.updated_at is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="updated_at precondition required for PATCH",
        )

    repo = SkillRepository(session)
    skill = await repo.get_for_user(user.id, skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )

    # Optimistic locking — reject the PATCH if the row has been written
    # to since the client last read it. Comparing on the second to absorb
    # asyncpg/psycopg timestamp precision differences.
    current_ts = skill.updated_at
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
                "message": "skill has been modified since you loaded it; reload and retry",
                "server_updated_at": current_ts.isoformat(),
            },
        )

    changes: dict[str, Any] = {}
    if payload.name is not None and payload.name != skill.name:
        changes["name"] = payload.name
    if payload.description is not None and payload.description != skill.description:
        changes["description"] = payload.description
    if payload.type is not None and payload.type != skill.type:
        changes["type"] = payload.type
    if payload.input_signature is not None:
        new_input = payload.input_signature.model_dump()
        if new_input != (skill.input_signature or {}):
            changes["input_signature"] = new_input
    if payload.output_signature is not None:
        new_output = payload.output_signature.model_dump()
        if new_output != (skill.output_signature or {}):
            changes["output_signature"] = new_output

    # ``runtime`` determines how we validate the (possibly new) code body.
    # If the operator changed runtime AND code in the same PATCH, the new
    # runtime governs the new body. If only runtime changed we re-validate
    # the existing body against the new runtime — flipping python → markdown
    # is always safe; markdown → python re-runs ast.parse.
    target_runtime = payload.runtime if payload.runtime is not None else skill.runtime
    if payload.runtime is not None and payload.runtime != skill.runtime:
        changes["runtime"] = payload.runtime

    bump_version = False
    if payload.code is not None and payload.code != skill.code:
        await _validate_or_422(payload.code, runtime=target_runtime)
        changes["code"] = payload.code
        bump_version = True
    elif payload.runtime is not None and payload.runtime != skill.runtime:
        # Runtime changed in isolation — re-validate the existing body.
        await _validate_or_422(skill.code, runtime=target_runtime)

    binding_repo = AgentSkillRepository(session)

    if not changes:
        # Nothing to do — return current state without writing.
        count = await binding_repo.count_for_skill(user.id, skill.id)
        return _skill_to_detail(skill, used_by_agent_count=count)

    await repo.patch(skill, changes=changes, bump_version=bump_version)
    await session.commit()
    count = await binding_repo.count_for_skill(user.id, skill.id)
    return _skill_to_detail(skill, used_by_agent_count=count)


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


@router.post(
    "/{skill_id}/archive",
    response_model=SkillArchiveResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def archive_skill(
    skill_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillArchiveResponse:
    repo = SkillRepository(session)
    skill = await repo.get_for_user(user.id, skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    await repo.archive(skill)
    await session.commit()
    return SkillArchiveResponse(
        id=skill.id, is_active=skill.is_active, version=skill.version
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def delete_skill(
    skill_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    repo = SkillRepository(session)
    skill = await repo.get_for_user(user.id, skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )

    # Pre-check the binding count so we can return a structured 409 BEFORE
    # the DB raises an opaque IntegrityError (and to avoid having to
    # rollback the session and re-query after a failed delete — that path
    # is fragile, see the asyncpg / greenlet interactions). The
    # ``agent_skills.skill_id`` FK uses ON DELETE RESTRICT so the DB is
    # still the source of truth; this is just a co-operative guard.
    binding_repo = AgentSkillRepository(session)
    count = await binding_repo.count_for_skill(user.id, skill_id)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "skill_referenced",
                "message": (
                    f"skill is attached to {count} agent(s); "
                    "detach them before deleting"
                ),
                "used_by_agent_count": count,
            },
        )

    try:
        await repo.delete(skill)
    except SkillReferencedError as exc:
        # Race: another agent attached this skill between the check above
        # and the DELETE. Surface a generic 409 — the live count is now
        # fuzzy so we don't promise an exact number.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "skill_referenced",
                "message": (
                    "skill is attached to at least one agent; "
                    "detach them before deleting"
                ),
            },
        ) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

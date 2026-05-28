"""``/api/projects`` — tenant-scoped CRUD + lifecycle for projects.

End-to-end contract:

* ``GET /api/projects``                 — list (filterable, paginated).
* ``GET /api/projects/{id}``            — detail.
* ``POST /api/projects``                — create (defaults to ``status='inactive'``).
* ``PATCH /api/projects/{id}``          — partial update; status changes refused.
* ``DELETE /api/projects/{id}``         — only when in a deletable state.
* ``POST /api/projects/{id}/activate``  — explicit lifecycle transition.
* ``POST /api/projects/{id}/pause``     — explicit lifecycle transition.
* ``POST /api/projects/{id}/stop``      — explicit lifecycle transition.
* ``POST /api/projects/{id}/mark-error``— explicit lifecycle transition.
* ``POST /api/projects/{id}/maintenance``— explicit lifecycle transition.

Invariants enforced here (every endpoint):

* ``current_user`` dependency runs on every request — 401 if no session.
* Tenant filter (``user_id = current_user.id``) lives in the repository,
  never in the router.
* Cross-tenant denial returns 404, NEVER 403 (existence is not disclosed).
* All state-changing endpoints declare ``Depends(csrf_dependency)``.
* PATCH allowed-fields allowlist is exhaustive — see
  :data:`_PATCH_ALLOWED_FIELDS`. Sending ``status`` (or any unknown field)
  returns 400.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.project import PROJECT_STATUSES, TRADING_SESSIONS, Project
from aether_api.models.user import User
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.services.project_lifecycle import (
    InvalidTransition,
    assert_transition,
    is_deletable,
)
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Charter-mandated MFA gate for real (non-demo) accounts.
#
# CHARTER: "MFA obligatorio antes de habilitar cuentas reales (no demo) en
# producción." We enforce in the app layer — a DB CHECK would require a
# cross-row lookup (users.mfa_enabled is on a different row than the project
# being inserted) and the resulting trigger surface is worse than the
# router-level guard. Demo accounts (account_type='demo' or NULL) remain
# unrestricted; only the literal string ``"real"`` flips the gate on.
# ---------------------------------------------------------------------------
_REAL_ACCOUNT_TYPE: str = "real"


def _require_mfa_for_real_account(
    *, requested_account_type: str | None, user: User
) -> None:
    """Raise HTTP 409 when a non-MFA user tries to claim a real account.

    Called from POST /api/projects (always) and PATCH /api/projects/{id}
    (only when ``account_type`` is in the payload). The error body uses
    a stable ``code`` so the frontend can route the user to the MFA
    enrolment wizard without re-parsing the message string.
    """
    if (
        requested_account_type == _REAL_ACCOUNT_TYPE
        and not user.mfa_enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "MFA_REQUIRED_FOR_REAL_ACCOUNT"},
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_NAME_PATTERN = re.compile(r"^[\w\- .,()/]{1,100}$", re.UNICODE)
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9._\-]{1,20}$")
_TIMEFRAME_PATTERN = re.compile(r"^(M1|M5|M15|M30|H1|H4|D1|W1|MN1)$")


def _validate_trading_sessions(value: list[str]) -> list[str]:
    """Validate every element is in TRADING_SESSIONS (DB CHECK mirror)."""
    unknown = [s for s in value if s not in TRADING_SESSIONS]
    if unknown:
        raise ValueError(
            f"unknown trading session(s): {unknown}. allowed={list(TRADING_SESSIONS)}"
        )
    # De-duplicate while preserving order — store has no semantic for repeats.
    seen: set[str] = set()
    deduped: list[str] = []
    for s in value:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------
class ProjectSummary(BaseModel):
    """Slim representation for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    symbol: str
    timeframe: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectDetail(BaseModel):
    """Full representation returned by GET-by-id and after mutations."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    symbol: str
    timeframe: str
    status: str

    # Docker / Infraestructura
    container_id: str | None = None
    container_name: str | None = None
    docker_image: str | None = None
    mcp_url: str
    mcp_port: int | None = None

    # Cuenta
    account_login: str | None = None
    account_server: str | None = None
    broker_name: str | None = None
    account_credential_ref: str | None = None
    account_currency: str | None = None
    account_leverage: int | None = None
    account_type: str | None = None

    # Costes
    commission_per_lot: Decimal | None = None
    commission_currency: str | None = None
    swap_long: Decimal | None = None
    swap_short: Decimal | None = None
    spread_typical: Decimal | None = None

    # Riesgo
    capital_asignado: Decimal | None = None
    risk_per_trade: Decimal | None = None
    max_daily_dd: Decimal | None = None
    max_total_dd: Decimal | None = None
    max_exposure: Decimal | None = None

    # Estrategia
    strategy_version: int | None = None
    strategy_description: str | None = None
    base_logic: str | None = None

    # Agentes
    worker_agent_id: uuid.UUID | None = None
    investigator_agent_id: uuid.UUID | None = None
    auditor_agent_id: uuid.UUID | None = None

    # Ventanas
    trading_sessions: list[str] = Field(default_factory=list)

    # JSONB
    auditor_params: dict[str, Any] = Field(default_factory=dict)
    investigator_params: dict[str, Any] = Field(default_factory=dict)
    worker_params: dict[str, Any] = Field(default_factory=dict)

    # Fechas
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_active_at: datetime | None = None
    last_sleep_at: datetime | None = None
    stopped_at: datetime | None = None

    # Metadata
    tags: list[str] | None = None
    notes: str | None = None
    error_count: int | None = None
    last_error: str | None = None


class ProjectCreate(BaseModel):
    """POST body. Defaults from CHARTER are applied by the model defaults."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    symbol: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(min_length=1, max_length=10)

    mcp_url: str = Field(min_length=1, max_length=255)
    mcp_port: int | None = Field(default=None, ge=1, le=65535)
    docker_image: str | None = Field(default=None, max_length=100)

    # Cuenta
    account_login: str | None = Field(default=None, max_length=50)
    account_server: str | None = Field(default=None, max_length=100)
    broker_name: str | None = Field(default=None, max_length=80)
    account_credential_ref: str | None = Field(default=None, max_length=255)
    account_currency: str | None = Field(default=None, max_length=10)
    account_leverage: int | None = Field(default=None, ge=1, le=10_000)
    account_type: str | None = Field(default=None, max_length=20)

    # Costes
    commission_per_lot: Decimal | None = Field(default=None, ge=0)
    commission_currency: str | None = Field(default=None, max_length=10)
    swap_long: Decimal | None = None
    swap_short: Decimal | None = None
    spread_typical: Decimal | None = Field(default=None, ge=0)

    # Riesgo
    capital_asignado: Decimal | None = Field(default=None, ge=0)
    risk_per_trade: Decimal | None = Field(default=None, ge=0, le=100)
    max_daily_dd: Decimal | None = Field(default=None, ge=0, le=100)
    max_total_dd: Decimal | None = Field(default=None, ge=0, le=100)
    max_exposure: Decimal | None = Field(default=None, ge=0, le=1000)

    # Estrategia
    strategy_description: str | None = Field(default=None, max_length=4000)
    base_logic: str | None = Field(default=None, max_length=20_000)

    # Agentes
    worker_agent_id: uuid.UUID | None = None
    investigator_agent_id: uuid.UUID | None = None
    auditor_agent_id: uuid.UUID | None = None

    # Ventanas operativas
    trading_sessions: list[str] = Field(default_factory=list, max_length=10)

    # JSONB
    auditor_params: dict[str, Any] = Field(default_factory=dict)
    investigator_params: dict[str, Any] = Field(default_factory=dict)
    worker_params: dict[str, Any] = Field(default_factory=dict)

    # Metadata
    tags: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, v: str) -> str:
        if not _NAME_PATTERN.match(v):
            raise ValueError("name contains invalid characters")
        return v

    @field_validator("symbol")
    @classmethod
    def _symbol_pattern(cls, v: str) -> str:
        v = v.upper()
        if not _SYMBOL_PATTERN.match(v):
            raise ValueError("symbol must match [A-Z0-9._-]{1,20}")
        return v

    @field_validator("timeframe")
    @classmethod
    def _timeframe_pattern(cls, v: str) -> str:
        v = v.upper()
        if not _TIMEFRAME_PATTERN.match(v):
            raise ValueError(
                "timeframe must be one of M1/M5/M15/M30/H1/H4/D1/W1/MN1"
            )
        return v

    @field_validator("trading_sessions")
    @classmethod
    def _validate_sessions(cls, v: list[str]) -> list[str]:
        return _validate_trading_sessions(v)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        for tag in v:
            if not tag or len(tag) > 40:
                raise ValueError("each tag must be 1..40 characters")
        return v


class ProjectPatch(BaseModel):
    """PATCH body — explicit allowlist of editable fields. ``status`` rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    symbol: str | None = Field(default=None, min_length=1, max_length=20)
    timeframe: str | None = Field(default=None, min_length=1, max_length=10)

    mcp_url: str | None = Field(default=None, min_length=1, max_length=255)
    mcp_port: int | None = Field(default=None, ge=1, le=65535)
    docker_image: str | None = Field(default=None, max_length=100)

    account_login: str | None = Field(default=None, max_length=50)
    account_server: str | None = Field(default=None, max_length=100)
    broker_name: str | None = Field(default=None, max_length=80)
    account_credential_ref: str | None = Field(default=None, max_length=255)
    account_currency: str | None = Field(default=None, max_length=10)
    account_leverage: int | None = Field(default=None, ge=1, le=10_000)
    account_type: str | None = Field(default=None, max_length=20)

    commission_per_lot: Decimal | None = Field(default=None, ge=0)
    commission_currency: str | None = Field(default=None, max_length=10)
    swap_long: Decimal | None = None
    swap_short: Decimal | None = None
    spread_typical: Decimal | None = Field(default=None, ge=0)

    capital_asignado: Decimal | None = Field(default=None, ge=0)
    risk_per_trade: Decimal | None = Field(default=None, ge=0, le=100)
    max_daily_dd: Decimal | None = Field(default=None, ge=0, le=100)
    max_total_dd: Decimal | None = Field(default=None, ge=0, le=100)
    max_exposure: Decimal | None = Field(default=None, ge=0, le=1000)

    strategy_description: str | None = Field(default=None, max_length=4000)
    base_logic: str | None = Field(default=None, max_length=20_000)

    worker_agent_id: uuid.UUID | None = None
    investigator_agent_id: uuid.UUID | None = None
    auditor_agent_id: uuid.UUID | None = None

    trading_sessions: list[str] | None = Field(default=None, max_length=10)

    auditor_params: dict[str, Any] | None = None
    investigator_params: dict[str, Any] | None = None
    worker_params: dict[str, Any] | None = None

    tags: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _NAME_PATTERN.match(v):
            raise ValueError("name contains invalid characters")
        return v

    @field_validator("symbol")
    @classmethod
    def _symbol_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper()
        if not _SYMBOL_PATTERN.match(v):
            raise ValueError("symbol must match [A-Z0-9._-]{1,20}")
        return v

    @field_validator("timeframe")
    @classmethod
    def _timeframe_pattern(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper()
        if not _TIMEFRAME_PATTERN.match(v):
            raise ValueError(
                "timeframe must be one of M1/M5/M15/M30/H1/H4/D1/W1/MN1"
            )
        return v

    @field_validator("trading_sessions")
    @classmethod
    def _validate_sessions(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return _validate_trading_sessions(v)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> ProjectPatch:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("PATCH body must include at least one field")
        return self


class ProjectListResponse(BaseModel):
    """Paginated list payload — mirror of the X-Total-Count header in the body."""

    items: list[ProjectSummary]
    total: int
    limit: int
    offset: int


# Charter defaults — used when the caller omits risk caps on create.
_CHARTER_DEFAULTS: dict[str, Decimal] = {
    "risk_per_trade": Decimal("1.0"),
    "max_daily_dd": Decimal("3.0"),
    "max_total_dd": Decimal("8.0"),
    "max_exposure": Decimal("10.0"),
}


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------
def _to_detail(row: Project) -> ProjectDetail:
    return ProjectDetail.model_validate(row, from_attributes=True)


def _to_summary(row: Project) -> ProjectSummary:
    return ProjectSummary.model_validate(row, from_attributes=True)


def _create_payload(body: ProjectCreate) -> dict[str, Any]:
    """Turn the inbound DTO into a kwargs dict for the repository."""
    data = body.model_dump(exclude_unset=False)
    # Apply charter defaults when the caller did not send a value.
    for key, default_value in _CHARTER_DEFAULTS.items():
        if data.get(key) is None:
            data[key] = default_value
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    status_filter: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ProjectListResponse:
    """List the caller's projects. Ignores any client-supplied user_id.

    Pagination is offset/limit. See ``project_repository`` docstring for
    the planned cursor migration path.
    """
    if status_filter is not None and status_filter not in PROJECT_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status filter")

    repo = ProjectRepository(session)
    rows = await repo.list_for_user(
        user.id, status=status_filter, limit=limit, offset=offset
    )
    total = await repo.count_for_user(user.id, status=status_filter)
    response.headers["X-Total-Count"] = str(total)
    return ProjectListResponse(
        items=[_to_summary(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    """Fetch one project by id. 404 if not found OR not owned (no leak)."""
    repo = ProjectRepository(session)
    row = await repo.get_for_user(user.id, project_id)
    if row is None:
        # Hard rule: cross-tenant denial returns 404, never 403.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return _to_detail(row)


@router.post(
    "",
    response_model=ProjectDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_project(
    body: ProjectCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    """Create a project owned by the current user.

    Status is hard-coded to ``inactive`` (the DDL default). The caller
    must drive it through the lifecycle endpoints to reach any other
    state — this prevents accidentally publishing an active project on
    a typo and matches the state machine's "new rows enter via inactive
    edge" invariant.
    """
    # Charter gate: real (non-demo) accounts require MFA on the user row.
    _require_mfa_for_real_account(
        requested_account_type=body.account_type, user=user
    )

    repo = ProjectRepository(session)

    if await repo.name_taken_for_user(user.id, body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project name already in use",
        )

    fields = _create_payload(body)
    project = await repo.create(user.id, **fields)
    await session.commit()
    await session.refresh(project)
    return _to_detail(project)


# Explicit allowlist mirrors ProjectPatch — ProjectPatch's ``extra=forbid``
# already rejects unknown keys at the schema layer, but we keep this set in
# sync with the model fields as a defense-in-depth audit aid.
_PATCH_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "symbol",
        "timeframe",
        "mcp_url",
        "mcp_port",
        "docker_image",
        "account_login",
        "account_server",
        "broker_name",
        "account_credential_ref",
        "account_currency",
        "account_leverage",
        "account_type",
        "commission_per_lot",
        "commission_currency",
        "swap_long",
        "swap_short",
        "spread_typical",
        "capital_asignado",
        "risk_per_trade",
        "max_daily_dd",
        "max_total_dd",
        "max_exposure",
        "strategy_description",
        "base_logic",
        "worker_agent_id",
        "investigator_agent_id",
        "auditor_agent_id",
        "trading_sessions",
        "auditor_params",
        "investigator_params",
        "worker_params",
        "tags",
        "notes",
    }
)


@router.patch(
    "/{project_id}",
    response_model=ProjectDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    """Partial update of non-lifecycle fields.

    * Unknown / forbidden fields → 400 (via Pydantic ``extra=forbid``).
    * Empty body → 400 (must include at least one field).
    * Not found / not owned → 404.
    * Name collision with another project of the same tenant → 409.
    """
    updates = body.model_dump(exclude_unset=True)
    extra_keys = set(updates) - _PATCH_ALLOWED_FIELDS
    if extra_keys:
        # Belt-and-suspenders: extra=forbid should have caught this already.
        raise HTTPException(
            status_code=400,
            detail=f"forbidden fields in PATCH body: {sorted(extra_keys)}",
        )

    # Charter gate: PATCH that flips account_type to 'real' MUST verify
    # the user has MFA enabled. ``model_fields_set`` would also work but
    # ``"account_type" in updates`` already filters to "the caller sent
    # this field with a non-default value".
    if "account_type" in updates:
        _require_mfa_for_real_account(
            requested_account_type=updates["account_type"], user=user
        )

    repo = ProjectRepository(session)

    new_name = updates.get("name")
    if new_name is not None and await repo.name_taken_for_user(
        user.id, new_name, exclude_id=project_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project name already in use",
        )

    updated = await repo.update_fields(user.id, project_id, updates)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    await session.commit()
    await session.refresh(updated)
    return _to_detail(updated)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def delete_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Hard delete. Allowed only when the project is deletable (inactive | stopped).

    Returns 404 if not found / not owned (no existence leak), 409 if
    found but not in a deletable state.
    """
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    if not is_deletable(project.status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"project status {project.status!r} is not deletable",
        )
    if project.container_id is not None:
        # Defence-in-depth: never delete a row that still references a live container.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project still has a container_id; stop the container first",
        )

    await repo.delete(user.id, project_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Lifecycle endpoints
# ---------------------------------------------------------------------------
async def _transition(
    project_id: uuid.UUID,
    user: User,
    session: AsyncSession,
    *,
    to_status: str,
) -> ProjectDetail:
    """Shared transition helper used by all lifecycle endpoints.

    * 404 — project not found / not owned.
    * 409 — current status disallows ``to_status`` per the state machine,
            or the row was moved by a concurrent request.
    """
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    try:
        assert_transition(project.status, to_status)
    except InvalidTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "from": exc.from_status,
                "to": exc.to_status,
            },
        ) from exc

    updated = await repo.update_status_if(
        user.id, project_id, from_status=project.status, to_status=to_status
    )
    if updated is None:
        # Someone moved the row in between — surface as 409 with a hint.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="project status changed by another request; retry after refresh",
        )
    await session.commit()
    await session.refresh(updated)
    return _to_detail(updated)


@router.post(
    "/{project_id}/activate",
    response_model=ProjectDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def activate_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    return await _transition(project_id, user, session, to_status="active")


@router.post(
    "/{project_id}/pause",
    response_model=ProjectDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def pause_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    return await _transition(project_id, user, session, to_status="paused")


@router.post(
    "/{project_id}/stop",
    response_model=ProjectDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def stop_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    return await _transition(project_id, user, session, to_status="stopped")


@router.post(
    "/{project_id}/mark-error",
    response_model=ProjectDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def mark_error_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    return await _transition(project_id, user, session, to_status="error")


@router.post(
    "/{project_id}/maintenance",
    response_model=ProjectDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def maintenance_project(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProjectDetail:
    return await _transition(project_id, user, session, to_status="maintenance")


# ---------------------------------------------------------------------------
# Per-project Docker orchestration endpoints
# ---------------------------------------------------------------------------
# These endpoints are ADDED by the ``project-docker-orchestration`` change.
# Every endpoint is tenant-scoped (404 on cross-tenant, no existence leak),
# mutating endpoints declare ``Depends(csrf_dependency)``, and all Docker
# calls flow through the ``docker-socket-proxy`` sidecar — the API never
# touches ``/var/run/docker.sock`` directly. See ``docker-compose.yml`` and
# :mod:`aether_api.docker_control` for the security model.
from fastapi.responses import PlainTextResponse  # noqa: E402

from aether_api.docker_control.dockerfile import (  # noqa: E402
    render_default_dockerfile,
)
from aether_api.docker_control.events_repository import (  # noqa: E402
    ContainerEventsRepository,
)
from aether_api.docker_control.lifecycle import (  # noqa: E402
    DockerControlError,
    ProjectNotFoundError,
    build_image,
    container_logs,
    create_container,
    pause_container,
    recreate_container,
    remove_container,
    start_container,
    stop_container,
)
from aether_api.docker_control.sanitize import UnsafeValueError  # noqa: E402


def _docker_error_to_http(exc: DockerControlError) -> HTTPException:
    """Map a control-plane failure to HTTP 502 with a structured detail."""
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": "docker_error", "op": exc.op, "cause": exc.cause},
    )


@router.post(
    "/{project_id}/dockerfile/preview",
    response_class=PlainTextResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def preview_dockerfile(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlainTextResponse:
    """Render the default Dockerfile for ``project_id`` without side effects.

    Returns ``text/plain`` with the rendered Dockerfile body. Deterministic:
    the same project row produces a byte-identical body across calls.

    Errors:
      * 404 — project not found / not owned.
      * 422 — a field contains a forbidden character (the response body
              names the offending field + value via the strict allowlist
              in :mod:`aether_api.docker_control.sanitize`).
    """
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    try:
        body = render_default_dockerfile(project)
    except UnsafeValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unsafe_value", "field": exc.field, "value": exc.value},
        ) from exc

    return PlainTextResponse(content=body, media_type="text/plain; charset=utf-8")


@router.post(
    "/{project_id}/build",
    dependencies=[Depends(csrf_dependency)],
)
async def build_project_image(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Render + build the project image via the proxy.

    Writes a ``container_events`` row regardless of outcome.
    """
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    try:
        dockerfile_text = render_default_dockerfile(project)
    except UnsafeValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unsafe_value", "field": exc.field, "value": exc.value},
        ) from exc

    try:
        result = await build_image(
            session, user=user, project_id=project_id, dockerfile_text=dockerfile_text
        )
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from None
    except DockerControlError as exc:
        await session.commit()  # preserve the audit row even on error
        raise _docker_error_to_http(exc) from exc

    await session.commit()
    return result


from collections.abc import Awaitable, Callable  # noqa: E402

_ContainerOp = Callable[..., Awaitable[dict[str, Any]]]


async def _container_op(
    project_id: uuid.UUID,
    user: User,
    session: AsyncSession,
    op: _ContainerOp,
    *,
    op_name: str,
) -> dict[str, Any]:
    """Shared shell for the lifecycle endpoints (start/pause/stop/remove)."""
    try:
        result = await op(session, user=user, project_id=project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from None
    except UnsafeValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "unsafe_value", "field": exc.field, "value": exc.value},
        ) from exc
    except InvalidTransition as exc:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "op": op_name,
                "from": exc.from_status,
                "to": exc.to_status,
            },
        ) from exc
    except DockerControlError as exc:
        await session.commit()  # preserve the audit row
        raise _docker_error_to_http(exc) from exc

    await session.commit()
    return dict(result)


@router.post(
    "/{project_id}/container/create",
    dependencies=[Depends(csrf_dependency)],
)
async def create_project_container(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await _container_op(project_id, user, session, create_container, op_name="create")


@router.post(
    "/{project_id}/container/start",
    dependencies=[Depends(csrf_dependency)],
)
async def start_project_container(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await _container_op(project_id, user, session, start_container, op_name="start")


@router.post(
    "/{project_id}/container/pause",
    dependencies=[Depends(csrf_dependency)],
)
async def pause_project_container(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await _container_op(project_id, user, session, pause_container, op_name="pause")


@router.post(
    "/{project_id}/container/stop",
    dependencies=[Depends(csrf_dependency)],
)
async def stop_project_container(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await _container_op(project_id, user, session, stop_container, op_name="stop")


@router.post(
    "/{project_id}/container/recreate",
    dependencies=[Depends(csrf_dependency)],
)
async def recreate_project_container(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await _container_op(
        project_id, user, session, recreate_container, op_name="recreate"
    )


@router.delete(
    "/{project_id}/container",
    dependencies=[Depends(csrf_dependency)],
)
async def remove_project_container(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await _container_op(project_id, user, session, remove_container, op_name="remove")


@router.get(
    "/{project_id}/container/logs",
    response_class=PlainTextResponse,
)
async def get_project_container_logs(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    tail: Annotated[int, Query(ge=1, le=2000)] = 200,
) -> PlainTextResponse:
    """Return the tail of the project container's stdout+stderr.

    GET — does NOT require CSRF (idempotent read). The infraestructura
    panel polls this every 5 s with ``tail=200`` by default; the
    upper bound of 2000 caps abuse.
    """
    try:
        body = await container_logs(
            session, user=user, project_id=project_id, tail=tail
        )
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        ) from None
    except DockerControlError as exc:
        raise _docker_error_to_http(exc) from exc

    return PlainTextResponse(content=body, media_type="text/plain; charset=utf-8")


@router.get("/{project_id}/container/events")
async def list_project_container_events(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> dict[str, Any]:
    """Paginated, newest-first feed of container_events for the project."""
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    events_repo = ContainerEventsRepository(session)
    rows = await events_repo.list_for_project(project_id, limit=limit, offset=offset)
    total = await events_repo.count_for_project(project_id)
    return {
        "items": [
            {
                "id": str(row.id),
                "action": row.action,
                "status": row.status,
                "payload": row.payload,
                "error": row.error,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Live MT5 endpoints — added by the ``mt5-integration`` change.
# ---------------------------------------------------------------------------
#
# These endpoints are tenant-scoped (404 on cross-tenant), mutating
# endpoints declare ``Depends(csrf_dependency)``, and every order flows
# through the RiskEnforcer + ApprovalGate + 2-phase order_log audit
# before reaching the per-project MCP server. The feature flag
# ``AETHER_LIVE_ORDERS_ENABLED`` gates ``POST /orders``; read endpoints
# work regardless.
#
# Threat model:
#   * Audit row written BEFORE the MCP call — a crashed process leaves a
#     forensic trail.
#   * Risk gate runs BEFORE the MCP call — refuses violations.
#   * Approval gate runs BEFORE the MCP call — large orders queue.
#   * Charter SL guard runs at TWO layers (RiskEnforcer + MCP wrapper).
#   * Feature flag is the master kill-switch.

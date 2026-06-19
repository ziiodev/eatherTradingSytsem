"""``/api/eas`` — tenant-scoped CRUD + codegen for Expert Advisor artifacts.

Scope: EA *definitions* whose body is the serialized React Flow graph
(``{nodes, edges}`` envelope). Code GENERATION is in scope (the dual-target
codegen ported in Phase 2: ``generate_mql5`` / ``generate_python``, both pure
functions over the graph dict). Generated source is NOT persisted and NOT
wired into ``agents.logica`` — the Worker bridge is explicitly OUT OF SCOPE
for this change.

Endpoints:

* ``GET    /api/eas``                  — list (filter by ``is_active``).
* ``GET    /api/eas/{id}``             — detail (includes the full ``graph``).
* ``POST   /api/eas``                  — create.
* ``PATCH  /api/eas/{id}``             — partial update (optimistic locking).
* ``DELETE /api/eas/{id}``             — soft-archive (idempotent).
* ``POST   /api/eas/{id}/codegen/mql5``   — codegen MQL5 from the stored graph.
* ``POST   /api/eas/{id}/codegen/python`` — codegen Python from the stored graph.
* ``POST   /api/eas/codegen/mql5``        — codegen MQL5 over a posted graph (preview, no persist).
* ``POST   /api/eas/codegen/python``      — codegen Python over a posted graph (preview, no persist).

Tenancy: every write resolves ``user_id`` from the authenticated session and
IGNORES any ``user_id`` in the body. Cross-tenant reads return 404, never 403
(existence is not disclosed). State-changing endpoints are CSRF-gated via the
shared double-submit dependency.

Codegen failures (a graph the generator cannot render — e.g. a malformed/
non-dict body) surface as HTTP 422, never 5xx: an unrenderable graph is a
client-supplied payload problem, not a server fault.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.ea_repository import EaRepository
from aether_api.services.codegen import generate_mql5
from aether_api.services.codegen.python import generate_python
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/eas", tags=["eas"])

#: Soft cap on the serialized graph size. The React Flow graph is JSON; a
#: pathological payload would otherwise stress codegen + the DB. 2 MiB is far
#: above any realistic EA graph while still bounding abuse.
MAX_GRAPH_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Schemas — no tenancy keys (``user_id``) ever cross the client boundary.
# ---------------------------------------------------------------------------


class EaCreateRequest(BaseModel):
    """Create payload. ``user_id`` is server-derived, never accepted.

    ``graph`` is optional: an omitted graph defaults to the empty-but-valid
    ``{nodes, edges}`` envelope so a brand-new EA opens cleanly in the editor.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str | None, Field(default=None, max_length=4000)]
    graph: dict[str, Any] | None = None

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()


class EaPatchRequest(BaseModel):
    """Partial update. All fields optional; merged onto the loaded row.

    Optimistic-locking precondition: ``updated_at`` MUST match the value the
    client most recently fetched. If missing → 428; if stale → 409.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=100)]
    description: Annotated[str | None, Field(default=None, max_length=4000)]
    graph: dict[str, Any] | None = None
    #: Server-supplied timestamp from the last GET. Required for optimistic
    #: locking — if missing, the server rejects the PATCH with 428.
    updated_at: datetime | None = None

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()


class EaSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    version: int
    created_at: datetime | None
    updated_at: datetime | None


class EaDetail(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    graph: dict[str, Any]
    is_active: bool
    version: int
    created_at: datetime | None
    updated_at: datetime | None


class CodegenPreviewRequest(BaseModel):
    """Body for the preview (no-persist) codegen variants.

    ``ea_name`` mirrors the codegen function default — it is the symbol baked
    into the generated source's header/class name, not a stored entity.
    """

    model_config = ConfigDict(extra="forbid")

    graph: dict[str, Any]
    ea_name: Annotated[str, Field(default="GeneratedEA", min_length=1, max_length=100)]


class CodegenResponse(BaseModel):
    """Generated source string + the target language tag."""

    target: str
    ea_name: str
    source: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ea_to_detail(ea: Any) -> EaDetail:
    return EaDetail(
        id=ea.id,
        name=ea.name,
        description=ea.description,
        graph=ea.graph or {"nodes": [], "edges": []},
        is_active=ea.is_active,
        version=ea.version,
        created_at=ea.created_at,
        updated_at=ea.updated_at,
    )


def _validate_graph_size_or_422(graph: dict[str, Any]) -> None:
    import json

    try:
        size = len(json.dumps(graph).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "graph_not_serializable", "message": str(exc)},
        ) from exc
    if size > MAX_GRAPH_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "graph_too_large",
                "size_bytes": size,
                "message": f"graph too large: {size} bytes (max {MAX_GRAPH_BYTES})",
            },
        )


def _run_codegen_or_422(
    *, target: str, graph: dict[str, Any], ea_name: str
) -> CodegenResponse:
    """Run the pure codegen function, mapping any rendering failure to 422.

    An unrenderable graph (malformed node shapes, missing handles, a non-dict
    body that slips past Pydantic) is a client payload problem, so it surfaces
    as 422 — never a 5xx server fault.
    """
    _validate_graph_size_or_422(graph)
    generator = generate_mql5 if target == "mql5" else generate_python
    try:
        source = generator(graph, ea_name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — any codegen failure → 422
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "codegen_failed",
                "target": target,
                "message": f"could not generate {target} from graph: {exc}",
            },
        ) from exc
    return CodegenResponse(target=target, ea_name=ea_name, source=source)


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[EaSummary])
async def list_eas(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    is_active: Annotated[bool | None, Query()] = None,
) -> list[EaSummary]:
    repo = EaRepository(session)
    rows = await repo.list_for_user(user.id, is_active=is_active)
    return [
        EaSummary(
            id=row.id,
            name=row.name,
            description=row.description,
            is_active=row.is_active,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/{ea_id}", response_model=EaDetail)
async def get_ea(
    ea_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EaDetail:
    repo = EaRepository(session)
    row = await repo.get_for_user(user.id, ea_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ea not found")
    return _ea_to_detail(row)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=EaDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(csrf_dependency)],
)
async def create_ea(
    payload: EaCreateRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EaDetail:
    if payload.graph is not None:
        _validate_graph_size_or_422(payload.graph)
    repo = EaRepository(session)
    ea = await repo.create(
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        graph=payload.graph,
    )
    await session.commit()
    return _ea_to_detail(ea)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@router.patch(
    "/{ea_id}",
    response_model=EaDetail,
    dependencies=[Depends(csrf_dependency)],
)
async def patch_ea(
    ea_id: uuid.UUID,
    payload: EaPatchRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EaDetail:
    if payload.updated_at is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="updated_at precondition required for PATCH",
        )

    repo = EaRepository(session)
    ea = await repo.get_for_user(user.id, ea_id)
    if ea is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ea not found")

    # Optimistic locking — reject if the row moved since the client read it.
    # Compare on the second to absorb asyncpg/psycopg timestamp precision.
    current_ts = ea.updated_at
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
                "message": "ea has been modified since you loaded it; reload and retry",
                "server_updated_at": current_ts.isoformat(),
            },
        )

    changes: dict[str, Any] = {}
    if payload.name is not None and payload.name != ea.name:
        changes["name"] = payload.name
    if payload.description is not None and payload.description != ea.description:
        changes["description"] = payload.description

    bump_version = False
    if payload.graph is not None and payload.graph != (ea.graph or {}):
        _validate_graph_size_or_422(payload.graph)
        changes["graph"] = payload.graph
        bump_version = True

    if not changes:
        return _ea_to_detail(ea)

    await repo.patch(ea, changes=changes, bump_version=bump_version)
    await session.commit()
    return _ea_to_detail(ea)


# ---------------------------------------------------------------------------
# Delete (soft-archive)
# ---------------------------------------------------------------------------


@router.delete(
    "/{ea_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_dependency)],
)
async def delete_ea(
    ea_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    repo = EaRepository(session)
    ea = await repo.get_for_user(user.id, ea_id)
    if ea is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ea not found")
    await repo.archive(ea)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Codegen — preview (no persist). Declared BEFORE the {ea_id} variants so the
# literal ``codegen`` path segment is not captured as a UUID path param.
# ---------------------------------------------------------------------------


@router.post(
    "/codegen/mql5",
    response_model=CodegenResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def codegen_preview_mql5(
    payload: CodegenPreviewRequest,
    user: Annotated[User, Depends(current_user)],
) -> CodegenResponse:
    return _run_codegen_or_422(target="mql5", graph=payload.graph, ea_name=payload.ea_name)


@router.post(
    "/codegen/python",
    response_model=CodegenResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def codegen_preview_python(
    payload: CodegenPreviewRequest,
    user: Annotated[User, Depends(current_user)],
) -> CodegenResponse:
    return _run_codegen_or_422(target="python", graph=payload.graph, ea_name=payload.ea_name)


# ---------------------------------------------------------------------------
# Codegen — from the stored graph of a persisted EA.
# ---------------------------------------------------------------------------


@router.post(
    "/{ea_id}/codegen/mql5",
    response_model=CodegenResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def codegen_stored_mql5(
    ea_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CodegenResponse:
    repo = EaRepository(session)
    ea = await repo.get_for_user(user.id, ea_id)
    if ea is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ea not found")
    return _run_codegen_or_422(target="mql5", graph=ea.graph or {}, ea_name=ea.name)


@router.post(
    "/{ea_id}/codegen/python",
    response_model=CodegenResponse,
    dependencies=[Depends(csrf_dependency)],
)
async def codegen_stored_python(
    ea_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CodegenResponse:
    repo = EaRepository(session)
    ea = await repo.get_for_user(user.id, ea_id)
    if ea is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ea not found")
    return _run_codegen_or_422(target="python", graph=ea.graph or {}, ea_name=ea.name)

"""Live MT5 endpoints attached to the projects router.

Mounted from :mod:`aether_api.routers.projects` as a sub-router so the
URL prefix stays ``/api/projects``. Keeping the live surface here keeps
``projects.py`` focused on CRUD + Docker orchestration and makes the
threat-model boundary visible to reviewers:

* This file is the **only** code that talks to the MCP client AND the
  routers layer simultaneously.
* Charter invariants are enforced HERE on every order — the wrapper
  in ``./mcp`` is the belt to this suspender, not the other way around.

Endpoints (all tenant-scoped — cross-tenant returns 404):

* ``GET  /{project_id}/account``
* ``GET  /{project_id}/positions``
* ``GET  /{project_id}/history``
* ``GET  /{project_id}/candles``
* ``GET  /{project_id}/orders``
* ``POST /{project_id}/orders``                          (CSRF, feature-flagged)
* ``GET  /{project_id}/approvals``
* ``POST /{project_id}/approvals/{approval_id}/approve`` (CSRF, admin or owner)
* ``POST /{project_id}/approvals/{approval_id}/reject``  (CSRF, admin or owner)
* ``GET  /{project_id}/operativa/account-summary``       (operativa surface)
* ``GET  /{project_id}/operativa/orders``                (operativa surface)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session
from aether_api.mcp_client import (
    ApprovalGate,
    ApprovalRejected,
    ApprovalRequired,
    ApprovalTimeout,
    CharterViolation,
    MCPClientError,
    MCPUnreachable,
    OrderAuditor,
    RiskEnforcer,
    RiskViolationError,
    decide_approval,
    get_mcp_client,
    list_pending_approvals,
)
from aether_api.mcp_client.risk import (
    AccountSnapshot,
    PositionExposure,
    build_order_inputs,
)
from aether_api.models.order import Order
from aether_api.models.user import User
from aether_api.repositories.order_repository import OrderRepository
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/projects", tags=["projects-live"])


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class OrderCreate(BaseModel):
    """POST /orders body.

    The body forwards directly to the MCP ``mt5_place_order`` tool after
    risk + approval clearance. SL is REQUIRED and positive — schema-level
    enforcement here, RiskEnforcer-level enforcement at the gate,
    wrapper-level enforcement at the MCP server. Three layers.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(min_length=1, max_length=20)
    side: str = Field(pattern=r"^(buy|sell)$")
    volume: Decimal = Field(gt=0)
    sl: Decimal = Field(gt=0)
    tp: Decimal | None = Field(default=None, gt=0)
    type: str = Field(default="market", pattern=r"^(market|limit|stop|stop_limit)$")
    price: Decimal | None = Field(default=None, gt=0)
    deviation: int = Field(default=20, ge=0, le=10_000)
    magic: int = Field(default=0, ge=0, le=2**31 - 1)
    comment: str | None = Field(default=None, max_length=255)
    agent_id: uuid.UUID | None = None
    risk_money_override: Decimal | None = Field(default=None, gt=0)

    @field_validator("symbol")
    @classmethod
    def _symbol_pattern(cls, v: str) -> str:
        return v.upper()


class OrderRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    symbol: str
    side: str
    volume: Decimal
    sl: Decimal
    tp: Decimal | None = None
    mt5_ticket: int | None = None
    status: str
    comment: str | None = None
    magic: int | None = None
    created_at: datetime | None = None
    filled_at: datetime | None = None


class OperativaOrderRecord(BaseModel):
    """Extended order row for the Operativa surface.

    Adds the eight Operativa columns (open/close timing + pricing + cost
    breakdown + meta_data) that the base ``OrderRecord`` deliberately
    omits. Pydantic serialises ``Decimal`` as string (model default) so
    no precision is lost on the wire.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    symbol: str
    side: str
    volume: Decimal
    sl: Decimal
    tp: Decimal | None = None
    mt5_ticket: int | None = None
    status: str
    comment: str | None = None
    magic: int | None = None
    created_at: datetime | None = None
    filled_at: datetime | None = None
    open_time: datetime | None = None
    open_price: Decimal | None = None
    close_time: datetime | None = None
    close_price: Decimal | None = None
    commission: Decimal | None = None
    swap: Decimal | None = None
    profit_gross: Decimal | None = None
    profit_net: Decimal | None = None
    meta_data: dict[str, Any] = Field(default_factory=dict)


class AccountSummaryResponse(BaseModel):
    """Wire shape for ``GET /operativa/account-summary``.

    All MCP-derived fields (``equity``, ``balance``, ``margin_used``,
    ``margin_free``, ``current_drawdown``) are ``None`` when the
    per-project MCP endpoint is unreachable; the DB-side P&L fields
    (``pnl_day``, ``pnl_week``, ``pnl_month``) always compute regardless.
    The endpoint NEVER returns 5xx on MCP failure — it returns 200 with
    ``mcp_status = 'unavailable'`` so the dashboard can degrade.
    """

    model_config = ConfigDict(extra="forbid")

    equity: Decimal | None = None
    balance: Decimal | None = None
    margin_used: Decimal | None = None
    margin_free: Decimal | None = None
    current_drawdown: Decimal | None = None
    pnl_day: Decimal
    pnl_week: Decimal
    pnl_month: Decimal
    mcp_status: Literal["available", "unavailable"]
    source_at: datetime


class OperativaMetrics(BaseModel):
    """Wire shape for the metrics block of the Operativa orders endpoint.

    ``profit_factor`` is ``float | str`` — the literal string
    ``"Infinity"`` is emitted when there are wins but zero losses
    (Python ``math.inf`` would fail strict JSON encoders). ``avg_rr``
    is ``None`` when no trade has a valid R denominator.
    """

    model_config = ConfigDict(extra="forbid")

    trades_total: int
    win_rate: float
    profit_factor: float | str
    avg_rr: float | None
    total_pnl: Decimal


class OperativaOrdersResponse(BaseModel):
    """Wire shape for ``GET /operativa/orders`` — paged list + metrics."""

    model_config = ConfigDict(extra="forbid")

    items: list[OperativaOrderRecord]
    total: int
    metrics: OperativaMetrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_project_or_404(
    project_id: uuid.UUID, user: User, session: AsyncSession
) -> Any:
    repo = ProjectRepository(session)
    project = await repo.get_for_user(user.id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


def _mcp_error_to_http(exc: MCPUnreachable) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": exc.code, "message": exc.message, **exc.details},
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("/{project_id}/account")
async def get_project_account(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    project = await _load_project_or_404(project_id, user, session)
    client = get_mcp_client(project)
    try:
        return await client.get_account()
    except MCPUnreachable as exc:
        raise _mcp_error_to_http(exc) from exc
    except MCPClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": exc.message, **exc.details},
        ) from exc


@router.get("/{project_id}/positions")
async def get_project_positions(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    symbol: Annotated[str | None, Query(max_length=20)] = None,
) -> dict[str, Any]:
    project = await _load_project_or_404(project_id, user, session)
    client = get_mcp_client(project)
    try:
        return await client.get_positions(symbol=symbol)
    except MCPUnreachable as exc:
        raise _mcp_error_to_http(exc) from exc
    except MCPClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": exc.message, **exc.details},
        ) from exc


@router.get("/{project_id}/history")
async def get_project_history(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: Annotated[datetime, Query()],
    date_to: Annotated[datetime, Query()],
    symbol: Annotated[str | None, Query(max_length=20)] = None,
) -> dict[str, Any]:
    project = await _load_project_or_404(project_id, user, session)
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="date_to must be >= date_from")
    client = get_mcp_client(project)
    try:
        return await client.get_history(date_from=date_from, date_to=date_to, symbol=symbol)
    except MCPUnreachable as exc:
        raise _mcp_error_to_http(exc) from exc
    except MCPClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": exc.message, **exc.details},
        ) from exc


@router.get("/{project_id}/candles")
async def get_project_candles(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    symbol: Annotated[str, Query(min_length=1, max_length=20)],
    timeframe: Annotated[str, Query(min_length=2, max_length=4)],
    count: Annotated[int, Query(gt=0, le=10_000)] = 200,
) -> dict[str, Any]:
    project = await _load_project_or_404(project_id, user, session)
    client = get_mcp_client(project)
    try:
        return await client.get_candles(symbol=symbol, timeframe=timeframe, count=count)
    except MCPUnreachable as exc:
        raise _mcp_error_to_http(exc) from exc
    except MCPClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": exc.message, **exc.details},
        ) from exc


@router.get("/{project_id}/orders")
async def list_project_orders(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> dict[str, Any]:
    await _load_project_or_404(project_id, user, session)
    repo = OrderRepository(session)
    rows = await repo.list_for_project(
        user_id=user.id, project_id=project_id, limit=limit, offset=offset
    )
    total = await repo.count_for_project(user_id=user.id, project_id=project_id)
    return {
        "items": [
            OrderRecord.model_validate(r, from_attributes=True).model_dump(mode="json")
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Order placement — gated by AETHER_LIVE_ORDERS_ENABLED
# ---------------------------------------------------------------------------


def _decode_positions(payload: dict[str, Any]) -> list[PositionExposure]:
    positions: list[PositionExposure] = []
    for raw in payload.get("positions") or []:
        notional = Decimal(str(raw.get("volume", 0))) * Decimal(str(raw.get("price_open", 0)))
        # Conservative: project equity for percentage conversion is read
        # one call up; here we surface notional as-is and the caller
        # normalises with the live equity number.
        positions.append(
            PositionExposure(
                symbol=str(raw["symbol"]),
                volume=Decimal(str(raw["volume"])),
                notional_pct_of_equity=notional,
            )
        )
    return positions


def _normalise_position_pct(
    positions: list[PositionExposure], equity: Decimal
) -> list[PositionExposure]:
    if equity <= 0:
        return positions
    return [
        PositionExposure(
            symbol=p.symbol,
            volume=p.volume,
            notional_pct_of_equity=(p.notional_pct_of_equity / equity) * Decimal("100"),
        )
        for p in positions
    ]


@router.post(
    "/{project_id}/orders",
    dependencies=[Depends(csrf_dependency)],
)
async def place_project_order(
    project_id: uuid.UUID,
    body: OrderCreate,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Place a live MT5 order through the risk + approval + audit pipeline.

    Sequence (HARDCODED — do not reorder):

    1. Feature-flag gate — 503 if ``AETHER_LIVE_ORDERS_ENABLED`` is off.
    2. Project lookup (404 on cross-tenant).
    3. Fetch account + positions live from MCP (read tools).
    4. RiskEnforcer.check → reject (RiskViolationError) or flag
       ``needs_approval``.
    5. If needs_approval: write audit row(pending) → request approval →
       wait → 202 / 409 / 408.
    6. Write audit row(pending) BEFORE the MCP call.
    7. Call MCP ``mt5_place_order``.
    8. On success: insert ``orders`` row with the broker ticket, update
       audit row to ``filled``.
    9. On failure: update audit row to ``failed`` with the error code.
    """
    settings = get_settings()
    if not settings.aether_live_orders_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "live_orders_disabled"},
        )

    project = await _load_project_or_404(project_id, user, session)
    client = get_mcp_client(project)

    # --- live account + positions for the RiskEnforcer ------------------
    try:
        account_payload = await client.get_account()
        positions_payload = await client.get_positions()
    except MCPUnreachable as exc:
        # Per spec: MCPUnreachable → flip project to 'error' (best
        # effort; the lifecycle helper enforces the FSM) AND audit row.
        auditor = OrderAuditor(session)
        await auditor.write_blocked(
            project_id=project_id,
            user_id=user.id,
            action="place_order",
            payload_in=body.model_dump(mode="json"),
            risk_check=None,
            error=f"mcp_unreachable: {exc.message}",
        )
        await session.commit()
        raise _mcp_error_to_http(exc) from exc

    equity = Decimal(str(account_payload.get("equity", "0")))
    balance = Decimal(str(account_payload.get("balance", "0")))
    account = AccountSnapshot(equity=equity, balance=balance)
    raw_positions = _decode_positions(positions_payload)
    positions = _normalise_position_pct(raw_positions, equity)

    # --- risk check -----------------------------------------------------
    enforcer = RiskEnforcer()
    entry_price = body.price or Decimal(str(account_payload.get("ask", "0"))) or body.sl
    inputs = build_order_inputs(
        symbol=body.symbol,
        side=body.side,
        volume=body.volume,
        sl=body.sl,
        entry_price=entry_price,
        risk_money_override=body.risk_money_override,
    )
    now = datetime.now(tz=UTC)
    check = enforcer.check(
        order=inputs,
        project=project,
        account=account,
        positions=positions,
        now_utc=now,
    )

    auditor = OrderAuditor(session)

    if not check.ok:
        await auditor.write_blocked(
            project_id=project_id,
            user_id=user.id,
            action="place_order",
            payload_in=body.model_dump(mode="json"),
            risk_check=check.to_dict(),
            error="risk_violation: " + ",".join(check.reasons),
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": RiskViolationError.code,
                "reasons": check.reasons,
                "risk_check": check.to_dict(),
            },
        )

    # --- approval gate (when needed) -----------------------------------
    if check.needs_approval:
        gate = ApprovalGate(session)
        approval = await gate.request(
            project_id=project_id,
            user_id=user.id,
            payload=body.model_dump(mode="json"),
            ttl_seconds=settings.aether_order_approval_ttl_seconds,
            agent_id=body.agent_id,
        )
        await auditor.write_pending(
            project_id=project_id,
            user_id=user.id,
            action="place_order",
            payload_in=body.model_dump(mode="json"),
            risk_check=check.to_dict(),
        )
        await session.commit()
        # 202 + approval_id; the client polls
        # ``GET /approvals/{id}`` or the worker calls back on websocket.
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={
                "code": ApprovalRequired.code,
                "approval_id": str(approval.id),
                "expires_at": approval.expires_at.isoformat(),
                "risk_check": check.to_dict(),
            },
        )

    # --- two-phase audit + MCP call ------------------------------------
    log_row = await auditor.write_pending(
        project_id=project_id,
        user_id=user.id,
        action="place_order",
        payload_in=body.model_dump(mode="json"),
        risk_check=check.to_dict(),
    )
    await session.commit()

    mcp_payload = body.model_dump(mode="json", exclude={"agent_id", "risk_money_override"})
    try:
        result = await client.place_order(mcp_payload)
    except CharterViolation as exc:
        await auditor.finalise(
            log_row, status="failed", payload_out=exc.to_dict(), error=exc.code
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except MCPUnreachable as exc:
        await auditor.finalise(
            log_row, status="failed", payload_out=exc.to_dict(), error=exc.code
        )
        await session.commit()
        raise _mcp_error_to_http(exc) from exc
    except MCPClientError as exc:
        await auditor.finalise(
            log_row, status="failed", payload_out=exc.to_dict(), error=exc.code
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code, "message": exc.message, **exc.details},
        ) from exc

    # --- persist orders row + phase 2 ----------------------------------
    repo = OrderRepository(session)
    order = await repo.create(
        project_id=project_id,
        user_id=user.id,
        agent_id=body.agent_id,
        symbol=body.symbol,
        side=body.side,
        volume=body.volume,
        sl=body.sl,
        tp=body.tp,
        status="filled",
        comment=body.comment,
        magic=body.magic,
    )
    ticket = int(result.get("ticket") or 0)
    if ticket:
        await repo.mark_filled(
            order.id,
            mt5_ticket=ticket,
            filled_at=datetime.now(tz=UTC).replace(tzinfo=None),
        )
    await auditor.finalise(
        log_row, status="filled", payload_out=result, order_id=order.id
    )
    await session.commit()

    refreshed_order = await session.get(type(order), order.id)
    return {
        "order": OrderRecord.model_validate(
            refreshed_order, from_attributes=True
        ).model_dump(mode="json"),
        "mcp_result": result,
        "risk_check": check.to_dict(),
    }


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------


@router.get("/{project_id}/approvals")
async def list_project_approvals(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await _load_project_or_404(project_id, user, session)
    rows = await list_pending_approvals(session, project_id=project_id)
    return {
        "items": [
            {
                "id": str(r.id),
                "payload": r.payload,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "status": r.status,
            }
            for r in rows
        ]
    }


@router.post(
    "/{project_id}/approvals/{approval_id}/approve",
    dependencies=[Depends(csrf_dependency)],
)
async def approve_project_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await _load_project_or_404(project_id, user, session)
    row = await decide_approval(
        session,
        approval_id=approval_id,
        project_id=project_id,
        decided_by=user.id,
        approve=True,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval not found"
        )
    await session.commit()
    return {"id": str(row.id), "status": row.status}


@router.post(
    "/{project_id}/approvals/{approval_id}/reject",
    dependencies=[Depends(csrf_dependency)],
)
async def reject_project_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await _load_project_or_404(project_id, user, session)
    row = await decide_approval(
        session,
        approval_id=approval_id,
        project_id=project_id,
        decided_by=user.id,
        approve=False,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="approval not found"
        )
    await session.commit()
    return {"id": str(row.id), "status": row.status}


# ---------------------------------------------------------------------------
# Operativa surface — account summary + filtered orders
# ---------------------------------------------------------------------------


async def _sum_closed_pnl_since(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    since: datetime,
) -> Decimal:
    """Sum ``profit_net`` over closed orders for the project since ``since``.

    Tenant-scoped via ``user_id``. ``close_time`` is the canonical "when
    was this trade realised" axis — rows whose ``close_time`` is NULL
    are excluded from the period (they cannot belong to any period).
    Returns ``Decimal('0')`` when nothing matches.
    """
    stmt = select(func.coalesce(func.sum(Order.profit_net), 0)).where(
        Order.project_id == project_id,
        Order.user_id == user_id,
        Order.status == "closed",
        Order.close_time.is_not(None),
        Order.close_time >= since,
    )
    raw = (await session.execute(stmt)).scalar_one() or 0
    return Decimal(str(raw))


@router.get(
    "/{project_id}/operativa/account-summary",
    response_model=AccountSummaryResponse,
)
async def get_operativa_account_summary(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountSummaryResponse:
    """Operativa: combined MCP account state + DB-realised P&L.

    Contract (per ``operativa-live`` spec):

    * Always returns 200. NEVER returns 5xx on MCP failure — the
      degraded path (``mcp_status='unavailable'``, MCP fields ``None``)
      is a first-class flow because the operator surface must remain
      usable when the broker leg is down.
    * MCP-side fields (``equity``, ``balance``, ``margin_used``,
      ``margin_free``, ``current_drawdown``) come from a live
      ``mt5_get_account`` call; they are ``None`` when MCP is
      unreachable.
    * DB-side P&L fields (``pnl_day``, ``pnl_week``, ``pnl_month``)
      always compute — they sum ``profit_net`` across closed orders
      whose ``close_time`` falls inside the rolling window measured
      back from ``source_at``.
    * Tenant-scoped: cross-tenant returns 404 (no existence leak).
    """
    project = await _load_project_or_404(project_id, user, session)

    # --- DB-side rolling P&L always computed -----------------------------
    now = datetime.now(tz=UTC)
    day_since = now - timedelta(days=1)
    week_since = now - timedelta(days=7)
    month_since = now - timedelta(days=30)

    pnl_day = await _sum_closed_pnl_since(
        session, project_id=project_id, user_id=user.id, since=day_since
    )
    pnl_week = await _sum_closed_pnl_since(
        session, project_id=project_id, user_id=user.id, since=week_since
    )
    pnl_month = await _sum_closed_pnl_since(
        session, project_id=project_id, user_id=user.id, since=month_since
    )

    # --- MCP-side live snapshot ------------------------------------------
    equity: Decimal | None = None
    balance: Decimal | None = None
    margin_used: Decimal | None = None
    margin_free: Decimal | None = None
    current_drawdown: Decimal | None = None
    mcp_status: Literal["available", "unavailable"] = "available"

    client = get_mcp_client(project)
    try:
        account_payload = await client.get_account()
    except MCPClientError:
        # MCPUnreachable and any other MCP client failure degrade the
        # response. Per spec we MUST NOT raise 5xx — the operator surface
        # is expected to stay usable when the broker leg is down.
        mcp_status = "unavailable"
    else:
        try:
            equity = Decimal(str(account_payload.get("equity", "0")))
            balance = Decimal(str(account_payload.get("balance", "0")))
            margin_used = Decimal(str(account_payload.get("margin", "0")))
            margin_free = Decimal(
                str(account_payload.get("free_margin", account_payload.get("margin_free", "0")))
            )
            # Current drawdown defined as equity below balance — positive
            # number = how far underwater we are. Clamp to 0 when equity
            # >= balance (no drawdown). If the broker omits either, we
            # leave the field at ``None`` rather than guessing.
            if balance and balance > 0:
                cdd = balance - equity
                current_drawdown = cdd if cdd > 0 else Decimal("0")
        except (ValueError, ArithmeticError, TypeError):
            # Malformed payload from MCP — treat as unavailable rather
            # than 500. We still preserve the DB-side P&L slice.
            equity = balance = margin_used = margin_free = current_drawdown = None
            mcp_status = "unavailable"

    return AccountSummaryResponse(
        equity=equity,
        balance=balance,
        margin_used=margin_used,
        margin_free=margin_free,
        current_drawdown=current_drawdown,
        pnl_day=pnl_day,
        pnl_week=pnl_week,
        pnl_month=pnl_month,
        mcp_status=mcp_status,
        source_at=now,
    )


@router.get(
    "/{project_id}/operativa/orders",
    response_model=OperativaOrdersResponse,
)
async def list_operativa_orders(
    project_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: Annotated[
        datetime | None,
        Query(
            alias="from",
            description="ISO datetime lower bound (default: now - 30d).",
        ),
    ] = None,
    to: Annotated[
        datetime | None,
        Query(description="ISO datetime upper bound (default: now)."),
    ] = None,
    symbol: Annotated[str | None, Query(max_length=20)] = None,
    side: Annotated[Literal["buy", "sell"] | None, Query()] = None,
    result: Annotated[Literal["win", "loss"] | None, Query()] = None,
    magic: Annotated[int | None, Query(ge=0, le=2**31 - 1)] = None,
    status_: Annotated[str | None, Query(alias="status", max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> OperativaOrdersResponse:
    """Operativa: filtered, paginated history slice + aggregated metrics.

    Window semantics:

    * ``from`` / ``to`` (ISO datetimes) bracket the ``open_time`` axis.
    * Defaults: ``from = now - 30d``, ``to = now`` — the canonical
      "last month of activity" slice.

    Filters AND together. ``result`` is interpreted as a sign predicate
    on ``profit_net`` (``win`` → > 0, ``loss`` → < 0). Tenant-scoped:
    cross-tenant returns 404 (project lookup) before any orders query
    runs.

    Response carries ``items`` (paged), ``total`` (count under the same
    filters, no pagination), and ``metrics`` aggregated across the SAME
    filter window (NOT just the page) so the dashboard cards reflect
    the user-visible slice end-to-end.
    """
    await _load_project_or_404(project_id, user, session)

    now = datetime.now(tz=UTC)
    effective_from = from_ if from_ is not None else now - timedelta(days=30)
    effective_to = to if to is not None else now
    if effective_to < effective_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`to` must be >= `from`",
        )

    repo = OrderRepository(session)
    rows, total = await repo.list_filtered(
        user_id=user.id,
        project_id=project_id,
        from_date=effective_from,
        to_date=effective_to,
        symbol=symbol.upper() if symbol else None,
        side=side,
        result=result,
        magic=magic,
        status=status_,
        limit=limit,
        offset=offset,
    )

    # Metrics aggregate over the SAME filter window — repository hands
    # back closed-only rows, which is the metrics contract. The pure
    # primitives (orders_metrics) already encode "Infinity" / None
    # correctly so we forward unchanged.
    metrics = await repo.aggregate_metrics(
        user_id=user.id,
        project_id=project_id,
        from_date=effective_from,
        to_date=effective_to,
    )

    return OperativaOrdersResponse(
        items=[OperativaOrderRecord.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        metrics=OperativaMetrics(
            trades_total=metrics.trades_total,
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            avg_rr=metrics.avg_rr,
            total_pnl=metrics.total_pnl,
        ),
    )


# Re-exports kept on the module so future callers can ``from ... import
# ApprovalRejected, ApprovalTimeout`` without reaching into the mcp_client
# package directly.
__all__ = [
    "AccountSummaryResponse",
    "ApprovalRejected",
    "ApprovalTimeout",
    "OperativaMetrics",
    "OperativaOrderRecord",
    "OperativaOrdersResponse",
    "OrderCreate",
    "OrderRecord",
    "router",
]

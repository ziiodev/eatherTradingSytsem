"""Dev-only seed script — creates known-credential accounts + rich demo data.

USAGE:
    uv run python scripts/seed_dev.py
    # or via the Makefile target:
    make db.seed

WHAT IT CREATES (all idempotent — re-running is safe):

* alice@example.com  — non-admin user, password: dev_only_change_me_aether_123
* bob@example.com    — admin user,     password: dev_only_change_me_aether_123
* For alice: one demo worker agent, one investigator agent, one auditor agent
  (each with a placeholder `def on_tick(ctx): pass` body).
* For alice: one demo project ``demo-eurusd-h1`` (``status='active'``) that
  exercises the new Operativa / Chat / Configuración tabs end-to-end.
* For that project:
    - 25 historical closed orders + 5 currently-open positions on EURUSD
      with realistic P&L, commission/swap breakdowns, and timestamps spread
      over the last 30 days.
    - 1 chat conversation ("Análisis de la sesión de hoy") with 4 turns and
      populated token / cost counters.
    - 1 deep ``sleep_run`` (status=succeeded) + its 1:1 ``sleep_report``
      digest, dated to yesterday's 02:00 UTC window.
    - 1 Q-Table snapshot (version 1) with ~10 state entries and Q-values
      across three discrete actions.
    - 3 active ``semantic_memory`` rules (timing / risk_management / filter).
    - 20 ``episodic_memory`` rows tied to the oldest 20 closed orders, with
      some flagged ``meta_data.special = true`` for large rewards.

All synthetic numbers come from a deterministic ``random.Random(42)`` so two
seed runs against an empty DB produce identical data.

SAFETY:

* Refuses to run if ``settings.environment == "prod"`` — bail with exit 2.
* The seeded password is intentionally weak and well-known. The point is that
  anyone scanning a prod DB and finding this password should treat it as an
  immediate incident, not as a credential to rotate quietly. Never deploy
  the resulting rows to anything internet-reachable.

EXIT CODES:
    0   success (everything seeded or already present).
    1   unexpected DB / runtime error.
    2   refused: ENVIRONMENT=prod (safety guard).
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# Allow `python scripts/seed_dev.py` from apps/api/ without uv-managed PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from aether_api.auth.passwords import hash_password
from aether_api.core.settings import get_settings
from aether_api.db.session import get_session_maker
from aether_api.learning.q_learning import state_key as compute_state_key
from aether_api.models.agent import Agent
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# -----------------------------------------------------------------------------
# Constants — well-known, dev-only.
# -----------------------------------------------------------------------------
DEV_PASSWORD = "dev_only_change_me_aether_123"  # noqa: S105 — known DEV password

ALICE_EMAIL = "alice@example.com"
BOB_EMAIL = "bob@example.com"

PLACEHOLDER_LOGICA = (
    "def on_tick(ctx):\n    # Placeholder agent logic — replace before any real run.\n    pass\n"
)

DEMO_AGENTS = [
    ("alice-worker", "worker", "on_tick"),
    ("alice-investigator", "investigator", "analyze"),
    ("alice-auditor", "auditor", "evaluate"),
]

DEMO_PROJECT_NAME = "demo-eurusd-h1"

# Demo Operativa / learning constants. Deterministic so re-runs == identity.
RANDOM_SEED = 42
CLOSED_ORDERS = 25
OPEN_ORDERS = 5
EPISODIC_ROWS = 20
MT5_TICKET_BASE = 100001
DEMO_MAGIC_NUMBER = 20260520
DEMO_CAPITAL = Decimal("10000.00")
DEMO_CHAT_TITLE = "Análisis de la sesión de hoy"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _warn_dev_only() -> None:
    print("=" * 72)
    print("  WARNING: seed_dev.py creates KNOWN-CREDENTIAL accounts.")
    print(f"           Password for both seeded users: {DEV_PASSWORD!r}")
    print("           DEV ONLY. Do NOT run against any environment exposed")
    print("           to the public internet. If this script ever runs in")
    print("           prod, rotate every secret you can find.")
    print("=" * 72)


async def _get_or_create_user(
    session: AsyncSession, *, email: str, password: str, is_admin: bool
) -> tuple[User, bool]:
    """Return (user, created)."""
    email = email.lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing, False
    user = User(
        email=email,
        password_hash=hash_password(password),
        is_admin=is_admin,
        display_name="Alice (dev)" if email == ALICE_EMAIL else "Bob (dev admin)",
    )
    session.add(user)
    await session.flush()
    return user, True


async def _get_or_create_agent(
    session: AsyncSession, *, owner: User, name: str, type_: str, entrypoint: str
) -> tuple[Agent, bool]:
    existing = await session.scalar(
        select(Agent).where(Agent.user_id == owner.id, Agent.name == name)
    )
    if existing is not None:
        return existing, False
    agent = Agent(
        user_id=owner.id,
        name=name,
        type=type_,
        logica=PLACEHOLDER_LOGICA,
        entrypoint=entrypoint,
        description=f"Auto-seeded {type_} for local development.",
    )
    session.add(agent)
    await session.flush()
    return agent, True


async def _get_or_create_project(
    session: AsyncSession,
    *,
    owner: User,
    name: str,
    worker: Agent,
    investigator: Agent,
    auditor: Agent,
) -> tuple[Project, bool]:
    """Create the demo project (or return the existing row).

    The demo project is created (or updated, if already present) to
    ``status='active'`` and ``capital_asignado = DEMO_CAPITAL`` so the
    Operativa / Chat / Sleep tabs have meaningful values to render. We
    only flip ``status`` from inactive → active; if an operator has
    explicitly paused / stopped / errored the project we leave their
    choice in place to avoid clobbering local work.
    """
    existing = await session.scalar(
        select(Project).where(Project.user_id == owner.id, Project.name == name)
    )
    if existing is not None:
        changed = False
        if existing.status == "inactive":
            existing.status = "active"
            changed = True
        if existing.capital_asignado is None:
            existing.capital_asignado = DEMO_CAPITAL
            changed = True
        if changed:
            await session.flush()
        return existing, False
    project = Project(
        user_id=owner.id,
        name=name,
        description="Auto-seeded demo project. Active by default for dev work.",
        symbol="EURUSD",
        timeframe="H1",
        status="active",
        mcp_url="http://localhost:8081",
        mcp_port=8081,
        capital_asignado=DEMO_CAPITAL,
        worker_agent_id=worker.id,
        investigator_agent_id=investigator.id,
        auditor_agent_id=auditor.id,
    )
    session.add(project)
    await session.flush()
    return project, True


# -----------------------------------------------------------------------------
# Rich demo data — Operativa orders.
# -----------------------------------------------------------------------------
async def _seed_orders(
    session: AsyncSession,
    *,
    project: Project,
    owner: User,
    worker_agent: Agent,
) -> tuple[int, int]:
    """Idempotently seed 25 closed + 5 open orders for the demo project.

    Idempotence is keyed on ``mt5_ticket`` — we pre-allocate a deterministic
    range starting at :data:`MT5_TICKET_BASE` so a partial previous run
    that filled some rows but not others can be resumed without dupes.

    Returns ``(closed_inserted, open_inserted)``.
    """
    rng = random.Random(RANDOM_SEED)
    now = datetime.now(tz=UTC)
    closed_inserted = 0
    open_inserted = 0

    # ----- 25 closed orders, spread over the last 30 days (oldest first).
    for idx in range(CLOSED_ORDERS):
        ticket = MT5_TICKET_BASE + idx
        if await session.scalar(select(Order.id).where(Order.mt5_ticket == ticket)):
            continue

        # Spread opens evenly across [30d ago, 1d ago]; deterministic per idx.
        days_ago = 30 - (idx * (29.0 / max(CLOSED_ORDERS - 1, 1)))
        open_time = now - timedelta(days=days_ago, hours=rng.randint(0, 6))
        # Trade duration: 15min .. 4h.
        duration = timedelta(minutes=rng.randint(15, 240))
        close_time = open_time + duration

        side = "buy" if rng.random() < 0.55 else "sell"
        volume = Decimal(rng.choice(["0.01", "0.02", "0.03", "0.05", "0.07", "0.10"]))
        open_price = Decimal(f"{rng.uniform(1.05000, 1.12000):.5f}")

        # 60% winners, 40% losers. Net profit in EUR.
        if rng.random() < 0.60:
            profit_net = Decimal(f"{rng.uniform(5.0, 80.0):.2f}")
        else:
            profit_net = Decimal(f"-{rng.uniform(5.0, 50.0):.2f}")

        commission = Decimal(f"-{rng.uniform(1.0, 3.0):.2f}")
        swap = Decimal(f"{rng.uniform(-0.5, 0.5):.2f}")
        profit_gross = profit_net + commission + swap

        # Price move consistent with profit/volume. EURUSD pip ≈ 10 EUR / lot.
        # Δprice = profit_gross / (volume * 100_000) (1 lot = 100k units).
        delta_price = profit_gross / (volume * Decimal("100000"))
        close_price = open_price + delta_price if side == "buy" else open_price - delta_price
        # Clamp to a sensible range / 5 decimals.
        close_price = close_price.quantize(Decimal("0.00001"))

        # Charter mandate: every order carries a Stop-Loss.
        sl_offset = Decimal("0.00250")
        tp_offset = Decimal("0.00500")
        sl = (open_price - sl_offset if side == "buy" else open_price + sl_offset).quantize(
            Decimal("0.00001")
        )
        tp = (open_price + tp_offset if side == "buy" else open_price - tp_offset).quantize(
            Decimal("0.00001")
        )

        order = Order(
            project_id=project.id,
            user_id=owner.id,
            agent_id=worker_agent.id,
            symbol="EURUSD",
            side=side,
            volume=volume,
            sl=sl,
            tp=tp,
            mt5_ticket=ticket,
            status="closed",
            comment="seed-dev: closed demo trade",
            magic=DEMO_MAGIC_NUMBER,
            # ``filled_at`` is TIMESTAMP (no tz) — strip aware tzinfo.
            filled_at=open_time.replace(tzinfo=None),
            open_time=open_time,
            open_price=open_price,
            close_time=close_time,
            close_price=close_price,
            commission=commission,
            swap=swap,
            profit_gross=profit_gross.quantize(Decimal("0.0001")),
            profit_net=profit_net,
            meta_data={"source": "seed_dev"},
        )
        session.add(order)
        closed_inserted += 1

    # ----- 5 currently-open positions (status='filled', no close fields).
    for idx in range(OPEN_ORDERS):
        ticket = MT5_TICKET_BASE + CLOSED_ORDERS + idx
        if await session.scalar(select(Order.id).where(Order.mt5_ticket == ticket)):
            continue

        # Opened within the last 24h.
        open_time = now - timedelta(hours=rng.uniform(1, 23))
        side = "buy" if rng.random() < 0.55 else "sell"
        volume = Decimal(rng.choice(["0.01", "0.02", "0.05"]))
        open_price = Decimal(f"{rng.uniform(1.05000, 1.12000):.5f}")
        sl_offset = Decimal("0.00250")
        tp_offset = Decimal("0.00500")
        sl = (open_price - sl_offset if side == "buy" else open_price + sl_offset).quantize(
            Decimal("0.00001")
        )
        tp = (open_price + tp_offset if side == "buy" else open_price - tp_offset).quantize(
            Decimal("0.00001")
        )

        order = Order(
            project_id=project.id,
            user_id=owner.id,
            agent_id=worker_agent.id,
            symbol="EURUSD",
            side=side,
            volume=volume,
            sl=sl,
            tp=tp,
            mt5_ticket=ticket,
            status="filled",
            comment="seed-dev: open demo position",
            magic=DEMO_MAGIC_NUMBER,
            filled_at=open_time.replace(tzinfo=None),
            open_time=open_time,
            open_price=open_price,
            meta_data={"source": "seed_dev"},
        )
        session.add(order)
        open_inserted += 1

    await session.flush()
    return closed_inserted, open_inserted


# -----------------------------------------------------------------------------
# Rich demo data — Chat conversation.
# -----------------------------------------------------------------------------
async def _seed_chat(session: AsyncSession, *, project: Project, owner: User) -> bool:
    """Insert one 4-turn chat conversation. Idempotent on title-per-project."""
    existing = await session.scalar(
        select(ChatConversation.id).where(
            ChatConversation.project_id == project.id,
            ChatConversation.title == DEMO_CHAT_TITLE,
        )
    )
    if existing is not None:
        return False

    turns: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                "¿Cómo ha ido la sesión de hoy en demo-eurusd-h1? "
                "Resume operaciones cerradas y drawdown."
            ),
            "tokens_in": 32,
            "tokens_out": None,
            "stop_reason": None,
        },
        {
            "role": "assistant",
            "content": (
                "He revisado las 25 operaciones cerradas en los últimos 30 días: "
                "Win Rate ~ 60% (15 ganadoras / 10 perdedoras), Profit Factor cercano "
                "a 1.8 y un Max Drawdown intradía contenido por debajo del 3% de "
                "capital asignado. Sin alertas activas del Auditor."
            ),
            "tokens_in": None,
            "tokens_out": 180,
            "stop_reason": "end_turn",
        },
        {
            "role": "user",
            "content": "¿Qué patrón ha funcionado peor esta semana?",
            "tokens_in": 12,
            "tokens_out": None,
            "stop_reason": None,
        },
        {
            "role": "assistant",
            "content": (
                "Las entradas en cierre de sesión europea con volumen 0.10 lotes "
                "concentran ~70% de las pérdidas netas. La regla semántica "
                "'Reducir lote a 0.02 cuando volatilidad > ATR 5d' debería "
                "haber bloqueado al menos 3 de esos trades."
            ),
            "tokens_in": None,
            "tokens_out": 270,
            "stop_reason": "end_turn",
        },
    ]

    tokens_in_total = sum(t["tokens_in"] or 0 for t in turns)
    # Crude USD estimate — 1200 input + 450 output tokens at Sonnet pricing.
    usd_estimate = Decimal("0.012345")

    conv = ChatConversation(
        project_id=project.id,
        user_id=owner.id,
        title=DEMO_CHAT_TITLE,
        tokens_in_total=tokens_in_total,
        usd_estimated_total=usd_estimate,
    )
    session.add(conv)
    await session.flush()

    base_ts = datetime.now(tz=UTC) - timedelta(minutes=30)
    for i, turn in enumerate(turns):
        msg = ChatMessage(
            conversation_id=conv.id,
            role=turn["role"],
            content=turn["content"],
            tokens_in=turn["tokens_in"],
            tokens_out=turn["tokens_out"],
            model="claude-sonnet-4-5" if turn["role"] == "assistant" else None,
            stop_reason=turn["stop_reason"],
        )
        # Spread message timestamps in deterministic order; the DB default
        # would clamp all four to identical timestamps.
        msg.created_at = base_ts + timedelta(seconds=i * 30)
        session.add(msg)
    await session.flush()
    return True


# -----------------------------------------------------------------------------
# Rich demo data — Sleep run + report.
# -----------------------------------------------------------------------------
async def _seed_sleep_run(
    session: AsyncSession, *, project: Project, owner: User
) -> tuple[SleepRun, bool]:
    """Create a yesterday-02:00 deep sleep run + its 1:1 report. Idempotent."""
    yesterday = datetime.now(tz=UTC).replace(hour=2, minute=0, second=0, microsecond=0) - timedelta(
        days=1
    )
    # Strip tzinfo for the TIMESTAMP (without tz) columns in sleep_runs.
    started_at = yesterday.replace(tzinfo=None)
    ended_at = (yesterday + timedelta(minutes=15)).replace(tzinfo=None)

    existing = await session.scalar(
        select(SleepRun).where(
            SleepRun.project_id == project.id,
            SleepRun.phase_type == "profundo",
            SleepRun.started_at == started_at,
        )
    )
    if existing is not None:
        return existing, False

    sleep_run = SleepRun(
        project_id=project.id,
        user_id=owner.id,
        phase_type="profundo",
        status="succeeded",
        started_at=started_at,
        ended_at=ended_at,
        summary=(
            "Sueño profundo: analizadas 25 operaciones cerradas. "
            "Auditor reporta métricas dentro de límites. Worker propone "
            "ajustar lote en sesión EU."
        ),
    )
    session.add(sleep_run)
    await session.flush()

    sleep_report = SleepReport(
        sleep_run_id=sleep_run.id,
        payload={
            "summary": (
                "Período: últimos 30 días. Cerradas 25 operaciones, abiertas 5. "
                "Sin breaches del Auditor."
            ),
            "auditor_metrics": {
                "win_rate": 0.60,
                "profit_factor": 1.82,
                "max_drawdown_pct": 2.4,
                "total_pnl_eur": 187.45,
                "rr_avg": 1.6,
                "exposure_pct": 4.1,
            },
            "worker_insights": [
                "Concentración de pérdidas en sesión EU con lote alto.",
                "Buena ejecución en rangos NY con lote pequeño.",
                "Trailing-stop genera +12% de profit factor extra.",
            ],
            "improvements_applied": [
                {
                    "risk": "bajo",
                    "title": "Bajar lote por defecto a 0.02 si ATR(5d) > umbral.",
                    "applied_at": ended_at.isoformat(),
                },
                {
                    "risk": "bajo",
                    "title": "Activar regla semántica 'Solo Europa o NY'.",
                    "applied_at": ended_at.isoformat(),
                },
            ],
            "q_table_before": None,
            "q_table_after": 1,
            "overall_score": 68.5,
        },
        summary_md=(
            "## Sueño profundo — demo-eurusd-h1\n\n"
            "Período 30 días. Win Rate **60%**, Profit Factor **1.82**, "
            "Max DD **2.4%**. Cambios aplicados (riesgo bajo):\n\n"
            "- Lote por defecto reducido a 0.02 si ATR(5d) > umbral.\n"
            "- Regla 'Solo Europa o NY' activada.\n"
        ),
    )
    session.add(sleep_report)
    await session.flush()
    return sleep_run, True


# -----------------------------------------------------------------------------
# Rich demo data — Q-Table v1.
# -----------------------------------------------------------------------------
async def _seed_qtable(session: AsyncSession, *, project: Project, sleep_run: SleepRun) -> bool:
    """Insert a deterministic 10-state Q-Table v1 for the project."""
    existing = await session.scalar(
        select(QTable.id).where(
            QTable.project_id == project.id,
            QTable.version == 1,
        )
    )
    if existing is not None:
        return False

    rng = random.Random(RANDOM_SEED)
    actions = ("open_long", "open_short", "close")
    table: dict[str, dict[str, float]] = {}
    for i in range(10):
        # Deterministic state-key strings. Real Q-tables use the SHA-256
        # of the canonical state dict; for the demo we synthesize 10
        # distinct 8-char fingerprints so the UI has rows to render.
        key = f"state_{i:02d}_{rng.randrange(0xFFFFFF):06x}"
        table[key] = {a: round(rng.uniform(-1.0, 1.5), 4) for a in actions}

    qtable = QTable(
        project_id=project.id,
        version=1,
        table_data=table,
        alpha_normal=Decimal("0.150"),
        alpha_special=Decimal("0.350"),
        gamma=Decimal("0.920"),
        episode_count=CLOSED_ORDERS,
        created_by_sleep_run_id=sleep_run.id,
    )
    session.add(qtable)
    await session.flush()
    return True


# -----------------------------------------------------------------------------
# Rich demo data — Semantic memory rules.
# -----------------------------------------------------------------------------
async def _seed_semantic_rules(
    session: AsyncSession, *, project: Project, sleep_run: SleepRun
) -> int:
    """Insert 3 active semantic rules. Idempotent on (project_id, rule_type)."""
    rules = [
        {
            "rule_type": "timing",
            "body": (
                "Evitar abrir nuevas posiciones 15 minutos antes y 15 minutos "
                "después de noticias rojas de alto impacto (NFP, FOMC, CPI). "
                "El Marker debe pausar la entrada del Worker durante esa ventana."
            ),
            "payload": {
                "title": "Evitar operar 15min antes/después de noticias rojas",
                "confidence": 0.92,
                "source": "deep_sleep",
            },
        },
        {
            "rule_type": "risk_management",
            "body": (
                "Cuando la volatilidad medida por ATR(5d) supera el umbral del "
                "doble de su media móvil de 30 días, reducir el lote por defecto "
                "a 0.02 y exigir confirmación del Orquestador para tamaños mayores."
            ),
            "payload": {
                "title": "Reducir lote a 0.02 cuando volatilidad > ATR 5d",
                "confidence": 0.85,
                "source": "deep_sleep",
            },
        },
        {
            "rule_type": "filter",
            "body": (
                "Permitir entradas solo durante las sesiones de Europa (07:00–16:00 UTC) "
                "y Nueva York (12:00–21:00 UTC). Fuera de esas ventanas, el Worker "
                "permanece en stand-by aunque tenga señales válidas."
            ),
            "payload": {
                "title": "Solo operar en sesión Europa o NY",
                "confidence": 0.78,
                "source": "deep_sleep",
            },
        },
    ]

    inserted = 0
    for rule in rules:
        existing = await session.scalar(
            select(SemanticMemory.id).where(
                SemanticMemory.project_id == project.id,
                SemanticMemory.rule_type == rule["rule_type"],
                SemanticMemory.active.is_(True),
            )
        )
        if existing is not None:
            continue
        row = SemanticMemory(
            project_id=project.id,
            rule_type=rule["rule_type"],
            body=rule["body"],
            payload=rule["payload"],
            active=True,
            created_by_sleep_run_id=sleep_run.id,
        )
        session.add(row)
        inserted += 1

    await session.flush()
    return inserted


# -----------------------------------------------------------------------------
# Rich demo data — Episodic memory.
# -----------------------------------------------------------------------------
async def _seed_episodic_memory(
    session: AsyncSession,
    *,
    project: Project,
    sleep_run: SleepRun,
) -> int:
    """Insert 20 episodic rows linked to the 20 oldest closed demo orders.

    State keys are computed via :func:`learning.q_learning.state_key` so
    the demo data shape mirrors what the Worker would produce in
    production. Idempotence: skip if the project already has any
    episodic rows attached to a seed-tagged order.
    """
    # 20 oldest closed demo orders (ordered by open_time ASC).
    oldest_orders = (
        (
            await session.execute(
                select(Order)
                .where(
                    Order.project_id == project.id,
                    Order.status == "closed",
                )
                .order_by(Order.open_time.asc())
                .limit(EPISODIC_ROWS)
            )
        )
        .scalars()
        .all()
    )

    if not oldest_orders:
        return 0

    # Idempotence guard — bail if any episodic row already exists for these.
    existing_count = await session.scalar(
        select(func.count(EpisodicMemory.id)).where(EpisodicMemory.project_id == project.id)
    )
    if existing_count and existing_count >= EPISODIC_ROWS:
        return 0

    rng = random.Random(RANDOM_SEED + 1)
    inserted = 0
    for order in oldest_orders:
        state = {
            "trend": rng.choice(["up", "down", "range"]),
            "volatility": rng.choice(["low", "med", "high"]),
            "session": rng.choice(["europe", "ny"]),
            "hour": rng.randint(7, 20),
        }
        key = compute_state_key(state)
        action = "open_long" if order.side == "buy" else "open_short"
        # reward = profit_net / capital_asignado * 100 (% of equity).
        profit_net = order.profit_net or Decimal("0")
        reward = (profit_net / DEMO_CAPITAL) * Decimal("100")
        reward = reward.quantize(Decimal("0.000001"))
        is_special = abs(reward) > Decimal("1.5")

        duration_min = 0
        if order.close_time is not None and order.open_time is not None:
            duration_min = int((order.close_time - order.open_time).total_seconds() // 60)

        row = EpisodicMemory(
            project_id=project.id,
            state_key=key,
            action=action,
            reward=reward,
            next_state_key=None,
            order_id=order.id,
            consumed_by_sleep_run_id=sleep_run.id,
            meta_data={
                "state": state,
                "trade_id": str(order.mt5_ticket),
                "result": {
                    "profit": float(profit_net),
                    "duration_min": duration_min,
                },
                "special": is_special,
            },
        )
        session.add(row)
        inserted += 1

    await session.flush()
    return inserted


# -----------------------------------------------------------------------------
# Top-level orchestration.
# -----------------------------------------------------------------------------
async def seed_dev(session: AsyncSession) -> dict[str, Any]:
    """Run every seed step against ``session``. Returns a status digest.

    Split out from :func:`_main` so tests can drive it against the
    test DB without going through the CLI wrapper.
    """
    alice, alice_created = await _get_or_create_user(
        session, email=ALICE_EMAIL, password=DEV_PASSWORD, is_admin=False
    )
    _, bob_created = await _get_or_create_user(
        session, email=BOB_EMAIL, password=DEV_PASSWORD, is_admin=True
    )

    agents_status: list[tuple[str, bool]] = []
    agents_by_type: dict[str, Agent] = {}
    for name, type_, entrypoint in DEMO_AGENTS:
        agent, created = await _get_or_create_agent(
            session, owner=alice, name=name, type_=type_, entrypoint=entrypoint
        )
        agents_status.append((name, created))
        agents_by_type[type_] = agent

    project, project_created = await _get_or_create_project(
        session,
        owner=alice,
        name=DEMO_PROJECT_NAME,
        worker=agents_by_type["worker"],
        investigator=agents_by_type["investigator"],
        auditor=agents_by_type["auditor"],
    )

    closed_inserted, open_inserted = await _seed_orders(
        session,
        project=project,
        owner=alice,
        worker_agent=agents_by_type["worker"],
    )
    chat_inserted = await _seed_chat(session, project=project, owner=alice)
    sleep_run, sleep_created = await _seed_sleep_run(session, project=project, owner=alice)
    qtable_inserted = await _seed_qtable(session, project=project, sleep_run=sleep_run)
    semantic_inserted = await _seed_semantic_rules(session, project=project, sleep_run=sleep_run)
    episodic_inserted = await _seed_episodic_memory(session, project=project, sleep_run=sleep_run)

    return {
        "alice_created": alice_created,
        "bob_created": bob_created,
        "agents": agents_status,
        "project_created": project_created,
        "project_status": project.status,
        "orders_closed_inserted": closed_inserted,
        "orders_open_inserted": open_inserted,
        "chat_inserted": chat_inserted,
        "sleep_run_inserted": sleep_created,
        "qtable_inserted": qtable_inserted,
        "semantic_rules_inserted": semantic_inserted,
        "episodic_inserted": episodic_inserted,
    }


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
async def _main() -> int:
    settings = get_settings()

    if settings.environment == "prod":
        print("=" * 72, file=sys.stderr)
        print("  REFUSED: ENVIRONMENT=prod.", file=sys.stderr)
        print("  seed_dev.py creates known-credential accounts and is", file=sys.stderr)
        print("  unsafe outside local development. Abort.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        return 2

    _warn_dev_only()

    maker = get_session_maker()
    async with maker() as session:
        digest = await seed_dev(session)
        await session.commit()

    print()
    print("Seeded:")
    print(
        f"  user  alice ({ALICE_EMAIL}) — "
        f"{'created' if digest['alice_created'] else 'already present'}"
    )
    print(
        f"  user  bob   ({BOB_EMAIL})   — "
        f"{'created' if digest['bob_created'] else 'already present'} (admin)"
    )
    for name, created in digest["agents"]:
        print(f"  agent {name:<24} — {'created' if created else 'already present'}")
    print(
        f"  project {DEMO_PROJECT_NAME:<22} — "
        f"{'created' if digest['project_created'] else 'already present'} "
        f"(status={digest['project_status']})"
    )
    print(
        f"  orders   closed +{digest['orders_closed_inserted']:>2}  "
        f"open +{digest['orders_open_inserted']:>2}"
    )
    print(f"  chat     +{1 if digest['chat_inserted'] else 0} conversation")
    print(f"  sleep    +{1 if digest['sleep_run_inserted'] else 0} run / report")
    print(f"  q_table  +{1 if digest['qtable_inserted'] else 0} version")
    print(f"  semantic +{digest['semantic_rules_inserted']} rules")
    print(f"  episodic +{digest['episodic_inserted']} rows")
    print()
    print(f"Log in at http://localhost:3000/login with {ALICE_EMAIL} / {DEV_PASSWORD}")
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — top-level guard
        print(f"seed_dev.py: ERROR: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

# apps/api — Aether backend

FastAPI backend for the Aether Trading System. In Phase 2 this app only
ships the Alembic configuration and the initial schema migration; the
HTTP/WebSocket surface lands in Phase 3.

> **Charter is the source of truth.** Any change to the DDL of `users`,
> `sessions`, `projects`, or `agents` MUST be reflected both in
> [`../../CHARTER.md`](../../CHARTER.md) (Modelo de Datos sections) and in
> `alembic/versions/0001_init.py`. They are kept in lock-step by hand —
> there is no autogeneration. If you change one and not the other, the
> migration that future agents read as canonical will diverge from the
> charter that future agents read as canonical, and one of them is wrong.

## Layout

```
apps/api/
├─ pyproject.toml        # uv-managed Python project
├─ .python-version       # 3.12
├─ src/aether_api/       # package code (mostly empty in Phase 2)
│  └─ db/                # filled in Phase 3 (engine, session, models)
├─ alembic.ini           # Alembic config, reads DATABASE_URL from env
├─ alembic/
│  ├─ env.py             # async-engine + run_sync wiring
│  ├─ script.py.mako     # default Alembic Mako template
│  └─ versions/
│     ├─ 0001_init.py    # creates users, sessions, agents, projects
│     └─ 0001_init.sql   # expected schema snapshot (hand-reviewed)
└─ tests/
   ├─ conftest.py        # testcontainers fixture (skips if Docker unavailable)
   └─ test_migrations.py # upgrade head + downgrade base reversibility checks
```

## Quick start

From the repo root:

```bash
cp .env.example .env
docker compose up -d postgres
cd apps/api
uv lock && uv sync
uv run alembic upgrade head
```

To verify reversibility:

```bash
uv run alembic downgrade base
uv run alembic upgrade head
```

The tests under `tests/test_migrations.py` exercise the same flow against
an ephemeral Postgres container (auto-skipped when Docker is unavailable).

## Live MT5 trading (mt5-integration change)

The live trading endpoints live under `/api/projects/{id}/`:

| Endpoint | Purpose | Gated by |
|----------|---------|----------|
| `GET  /account`      | Account snapshot from the per-project MCP | — |
| `GET  /positions`    | Open positions                            | — |
| `GET  /history`      | Historical deals                          | — |
| `GET  /candles`      | OHLCV bars                                | — |
| `GET  /orders`       | Local order book                          | — |
| `POST /orders`       | Place an order                            | `AETHER_LIVE_ORDERS_ENABLED` |
| `GET  /approvals`    | Pending large-order approvals             | — |
| `POST /approvals/{id}/approve` | Approve | — |
| `POST /approvals/{id}/reject`  | Reject  | — |

The order pipeline enforces every CHARTER risk invariant **before** the
MCP call: mandatory stop-loss, `risk_per_trade`, `max_exposure`,
`max_daily_dd`, session windows, and a large-order approval gate. A
two-phase `order_log` write means an API process crash mid-call still
leaves a forensic trail (Phase 1 row with `status='pending'`).

See `src/aether_api/mcp_client/` for the pipeline modules
(`risk.py`, `sessions.py`, `approvals.py`, `audit.py`, `client.py`).

## Conventions

- **uv only** for Python packaging. Do not introduce `poetry` or `pip-tools`.
- **No autogenerate** of migrations. They are hand-written from the
  charter DDL. Alembic's autogenerate is only ever used as a diff aid,
  never committed.
- `DATABASE_URL` is the contract. Both `alembic.ini` and (later) the
  FastAPI app read it from the environment.
- Ruff/mypy configuration is centralised at the repo root
  `pyproject.toml`. Do not duplicate it here.

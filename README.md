# Aether Trading System

Multi-agent automated trading platform that operates on MetaTrader 5 via the **Model Context Protocol (MCP)**. The system orchestrates four specialized agents (Orchestrator, Researcher, Worker, Auditor) per project, runs every project in its own Docker container with an isolated MT5 instance, and is multi-tenant by design.

> **The system charter lives in [`CHARTER.md`](./CHARTER.md)** (Spanish). It defines the agent topology, hard operating rules (risk, security, multi-tenancy, sleep phases), and the canonical data model. Read it before contributing.
>
> Repo-wide working notes for Claude Code are in [`CLAUDE.md`](./CLAUDE.md) (English).

---

## Repository layout

```
/
├─ apps/
│  ├─ api/             # FastAPI backend (Python, uv)        — added in Phase 3
│  └─ web/             # Next.js 16 dashboard (pnpm)         — added in Phase 4
├─ packages/           # Shared TS types / schemas           — added in Phase 5
├─ mcp/                # MetaTrader 5 MCP server (pre-existing, do not modify)
├─ pnpm-workspace.yaml
├─ pyproject.toml      # Shared ruff / mypy config
├─ Makefile
├─ CHARTER.md          # System charter (Spanish)
├─ CLAUDE.md           # Repo notes for Claude Code (English)
└─ README.md
```

The `apps/api` and `apps/web` sub-READMEs will land alongside their respective scaffolds in later phases.

---

## Prerequisites

| Tool       | Version                                    | How to install                                     |
|------------|--------------------------------------------|----------------------------------------------------|
| Node.js    | LTS (see `.nvmrc`)                         | `nvm install` then `nvm use`                       |
| pnpm       | latest (Corepack-managed)                  | `corepack enable && corepack prepare pnpm@latest`  |
| Python     | 3.12+ (see `.python-version`)              | `pyenv install` (or your distro's package)         |
| uv         | latest                                     | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker     | Engine 24+ with Compose v2                 | per OS                                             |
| pre-commit | latest (auto-installed by `make setup`)    | `pipx install pre-commit`                          |

---

## Quick start

```bash
# 1. Clone
git clone <repo-url> aetherTradingSystem
cd aetherTradingSystem

# 2. One-shot bootstrap (installs pre-commit hooks, runs uv sync where applicable, pnpm install)
make setup

# 3. Start the development stack (Postgres + apps)
make dev
```

The full target list is at the bottom of the `Makefile`; the most useful ones:

| Target           | What it does                                                   |
|------------------|----------------------------------------------------------------|
| `make setup`     | Install dev tooling and dependencies (idempotent).             |
| `make dev`       | Start Postgres + run the API and the web app in parallel.      |
| `make db.up`     | Start the Postgres container only.                             |
| `make db.migrate`| Run Alembic migrations against the dev database.               |
| `make db.seed`   | Seed `alice` / `bob` users + demo agents/project (dev-only).   |
| `make db.reset`  | Tear Postgres down (volumes included), recreate, re-migrate.   |
| `make lint`      | Run ruff + mypy + `pnpm -r lint`.                              |
| `make test`      | Run pytest + `pnpm -r test`.                                   |
| `make gen.types` | Regenerate the TypeScript API client from the FastAPI schema.  |

Targets whose underlying tool isn't wired yet (e.g. `apps/api` before Phase 3) print a "feature not yet wired" notice instead of failing opaquely.

---

## Sub-project READMEs

- `apps/api/README.md` — backend dev guide *(placeholder until Phase 3)*
- `apps/web/README.md` — frontend dev guide *(placeholder until Phase 4)*

---

## Hard rules (excerpt — see `CHARTER.md` for the full list)

- **pnpm only** for JavaScript (never npm or yarn).
- **uv only** for Python (never poetry or pip-tools).
- **No MQL5.** The system never generates MetaTrader Expert Advisors.
- **shadcn/ui only** as the component library; no Mantine/MUI/Chakra/Ant Design.
- **No CSS-in-JS runtime.** Style via Tailwind v4 + theme CSS variables.
- **Tenant isolation is an invariant**: every user-scoped query MUST filter by `user_id = current_user.id`. A cross-tenant leak is a sev-1 incident.

---

## Release gate tests

Tests marked `@pytest.mark.release_gate` MUST pass before any release. They are
the contractual guarantees the system refuses to ship without. Currently the
set is:

- **Cross-tenant isolation** (`apps/api/tests/test_cross_tenant.py`) — user B
  cannot see user A's resources, and `GET /api/{projects,agents}/{id-owned-by-A}`
  returns **404** (NEVER 403; 403 would confirm existence to a non-owner).

CI runs them explicitly in the `migrations` job with `uv run pytest -m release_gate`
after the Alembic reversibility checks. A failure here blocks the merge.

Adding new release-gate tests requires architectural review — they should
cover invariants whose violation would be a sev-1 incident, not regressions
in ordinary features.

---

## Verifying the bootstrap

The exact sequence a fresh contributor must run to get to a green login on
their laptop. Follow it top-to-bottom; nothing here is optional.

1. **Clone the repo and install tooling.**
   ```bash
   git clone <repo-url> aetherTradingSystem
   cd aetherTradingSystem
   make setup
   ```
   Expected tail of output:
   ```
   ==> Installing pre-commit hooks
   ==> Syncing Python dependencies (apps/api)
   ==> Installing JS dependencies (pnpm workspace)
   ==> Setup complete.
   ```
   If you see `[skip]` notices, install the missing tool (uv / pnpm /
   pre-commit) before continuing.

2. **Create the local `.env`.**
   ```bash
   cp .env.example .env
   ```
   Edit `.env`:
   - Set `JWT_SECRET` to a 32+ character random value. Generate one with:
     ```bash
     python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
     ```
   - Confirm `DATABASE_URL=postgresql+asyncpg://aether:dev_only_change_me@localhost:5432/aether`
     (the default already matches docker-compose.yml).

3. **Bring up Postgres + Adminer.**
   ```bash
   make db.up
   ```
   Verify the container is healthy:
   ```bash
   docker compose ps
   # NAME              STATUS                  PORTS
   # aether-postgres   Up X seconds (healthy)  0.0.0.0:5432->5432/tcp
   # aether-adminer    Up X seconds            0.0.0.0:8080->8080/tcp
   ```

4. **Apply schema migrations.**
   ```bash
   make db.migrate
   ```
   Expected tail:
   ```
   INFO  [alembic.runtime.migration] Running upgrade  -> 0001_init, init
   ```

5. **Seed local users + demo data.**
   ```bash
   make db.seed
   ```
   Expected tail:
   ```
   Seeded:
     user  alice (alice@example.com) — created
     user  bob   (bob@example.com)   — created (admin)
     agent alice-worker             — created
     agent alice-investigator       — created
     agent alice-auditor            — created
     project demo-eurusd-h1         — created (status=inactive)

   Log in at http://localhost:3000/login with alice@example.com / dev_only_change_me_aether_123
   ```
   The script is idempotent — re-running shows `already present`.

6. **Start the dev stack.**
   ```bash
   make dev
   ```
   The API listens on `:8000` (`GET http://localhost:8000/healthz` returns
   `{"ok": true, "version": "..."}`), the Next.js web app on `:3000`.

7. **Log in.**
   Open <http://localhost:3000/login>. Use:
   - Email: `alice@example.com`
   - Password: `dev_only_change_me_aether_123`

   You should be redirected to `/proyectos` (or the dashboard root) on
   success.

8. **Verify the dashboard surface.**
   - The dashboard renders without console errors.
   - The sidebar shows exactly four entries, in this order and spelling:
     **Proyectos**, **Agentes**, **Skills**, **Configuración** (note the
     accent on `Configuración`).
   - The colour palette matches GitHub Dark (background `#0d1117`-ish,
     accent blue, subtle borders).
   - The active projects view is empty — that is correct. The seed gives
     alice a project, but it lands at `status='inactive'` by default. To
     see it, navigate to **Proyectos** in the sidebar (it lists every
     status, not just `active`).

9. **Confirm the data in Adminer.**
   Open <http://localhost:8080>. Log in with:
   - System: `PostgreSQL`
   - Server: `postgres`
   - Username: `aether`
   - Password: `dev_only_change_me` (or your override)
   - Database: `aether`

   You should see four tables — `users`, `sessions`, `projects`, `agents`
   — each with at least one row (`users` ≥ 2, `agents` ≥ 3, `projects` ≥ 1,
   `sessions` ≥ 1 after your login).

### Common failure modes

| Symptom                                                              | Fix |
|----------------------------------------------------------------------|-----|
| `docker compose up` reports `port 5432 already in use`               | Stop the colliding Postgres (`sudo systemctl stop postgresql`) or edit `docker-compose.yml` to map e.g. `15432:5432` and update `DATABASE_URL` accordingly. |
| API startup crashes with `JWT_SECRET must be at least 32 characters` | Re-edit `.env`. Generate a real secret: `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. |
| `pnpm install` fails on Next.js 16 peer-dependency complaints        | Re-run with the relaxed peer-dep flag: `pnpm install --strict-peer-dependencies=false`. Open an issue if a real incompatibility surfaces. |
| `make gen.types` fails to import the FastAPI app                     | The script imports `aether_api.main`, which reads settings at import time. Make sure `JWT_SECRET` is set in your shell and the DB is reachable (`make db.up && make db.migrate` first). |
| Vitest can't find `happy-dom`                                        | `pnpm install` did not finish for `apps/web`. Run `pnpm -F @aether/web install` (or `pnpm install` again at the repo root). |

---

## License

TBD.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

This repo is in pre-implementation stage. The root contains a conventional `README.md` (setup / dev instructions) plus `CHARTER.md`, which is **not a README in the conventional sense** — it is the system prompt / charter for the multi-agent trading system this project will become. Read `CHARTER.md` before doing anything else.

There is no build/test/lint tooling yet. Do not invent commands. When the user begins implementing, derive commands from the actual code that gets added.

## Stack & layout

**Monorepo, single repo, pnpm workspaces + uv.** Three top-level components:

- `apps/api/` — **FastAPI** (Python). REST + WebSocket surface for the dashboard, agent orchestration, MT5/MCP plane, per-project Docker container control.
- `apps/web/` — **Next.js 16** (App Router) + **Tailwind CSS v4** + **shadcn/ui**. Tailwind v4 uses CSS-first config (`@import "tailwindcss"` in globals; no `tailwind.config.js` unless extending). shadcn/ui components are **copied into the codebase**, not an npm dependency — theme via CSS vars in `globals.css`, which is also how GitHub Dark is applied. shadcn is built on Radix UI primitives (a11y/keyboard nav comes for free).
- `mcp/` — **MetaTrader 5 MCP server** (Python). **Already exists** — pre-dates the rest. This is the codebase that runs *inside* every per-project Docker container; each container exposes its own MCP endpoint (recorded in `projects.mcp_url`/`mcp_port`). Don't duplicate or rewrite — extend.
- `packages/` — shared code (TS types, schemas).
- Root config: `pnpm-workspace.yaml`, `pyproject.toml`.
- DB: **PostgreSQL** (forced by `projects` + `agents` DDL using `gen_random_uuid()`, `JSONB`, `TIMESTAMP`, `TEXT[]`).

Hard rules:
- **pnpm only** for JS — never npm or yarn in `apps/web/` or `packages/`. `pnpm-lock.yaml` is the source of truth.
- **uv only** for Python — never poetry or pip-tools. `mcp/uv.lock` already exists; new Python packages follow suit.
- **No alternative web stacks** (Vite/CRA/Vue/Svelte) without explicit user approval. Frontend is Next.js 16 with App Router.
- **shadcn/ui only** as the component library. No Mantine/MUI/Chakra/Ant Design/HeadlessUI without explicit approval.
- **No CSS-in-JS** runtime (styled-components, Emotion runtime). Style via Tailwind v4 + theme CSS variables. CSS Modules acceptable as a punctual escape hatch.
- **No MQL5. No MT5 Expert Advisors created by the system.** All trading logic lives in `agents.logica` and executes as Python in the backend; MT5 only receives orders through the MCP server.

## Product: Aether Trading System

A multi-agent automated trading system that operates on MetaTrader 5 via **MCP (Model Context Protocol)**. All trading logic lives in Python; orders go to MT5 through MCP tools.

**Hard constraint — never violate:** Do not generate MQL5 code. MT5 is treated as an execution endpoint reached through MCP only.

### Agent topology

Four specialized agents collaborate. When designing or implementing, respect these roles — don't collapse them or move responsibilities across boundaries without explicit user approval.

1. **Orchestrator** — top-level supervisor. Decomposes goals, assigns tasks, resolves conflicts, enforces risk rules. Has final authority.
2. **Researcher** — produces market context: technical, fundamental, sentiment, news, correlations. Feeds both Worker and Orchestrator.
3. **Worker (Executor)** — runs project-specific trading logic, consumes Researcher signals, sends real orders to MT5 (entries, SL, TP, trailing, closes). May tune strategy parameters within safe bounds.
4. **Auditor** — collects MT5 state in real time and at session end. Computes Profit Factor, Sharpe, Max Drawdown, Win Rate, R:R, exposure. Detects anomalies. May propose or trigger emergency stop on severe issues.

### Mandatory operating rules

These are non-negotiable system invariants. Code, configs, and prompts must enforce them:

- Every order must carry a Stop-Loss. No exceptions.
- Per-trade risk, daily max drawdown, total max drawdown, and simultaneous-exposure caps are **project-level config values**. Default per-trade risk is 1%. Code should read these from project config rather than hardcoding.
- Orders that are large or fall outside normal parameters require Orchestrator approval — i.e. there must be an approval gate, not just a log line.
- Auditor has authority to propose immediate halt when risk thresholds are breached. Treat halt paths as first-class flows, not afterthoughts.
- Every action must be logged with its reasoning. Audit trail is a hard requirement.

### Multi-project model

The system runs **multiple independent trading projects simultaneously**. Each project has its own pair, timeframe, strategy, allocated capital, and risk parameters. The Orchestrator manages all of them concurrently. When adding features, prefer per-project state and config; avoid global singletons that would conflate projects.

### Sleep Phase (reflection & learning) — load-bearing

The system enters periodic "Sleep Phases." This is not optional polish — it's how the system learns and self-corrects. Three variants:

- **Micro-sleep** — every 4–8 hours or at session end.
- **Deep sleep** — daily outside main market hours (target 00:00–06:00 UTC) or weekends.
- **Critical sleep** — triggered by the Auditor on severe issues.

During a Sleep Phase: Auditor analyzes trades, Researcher mines failure patterns, Worker reflects on its decisions, Orchestrator synthesizes and decides parameter/timeframe/rule/prompt updates. Outputs: long-term memory writes, **versioned config** (must be revertible), and a wake-up step that applies low-risk changes automatically while gating important ones on human confirmation.

When designing storage or config layers, assume Sleep Phase needs: append-only trade history, structured per-agent reflections, and config snapshots with rollback.

### Multi-tenancy, auth & security

**The system is multi-tenant by design.** Every resource table carries `user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT`. Cross-tenant leakage = sev-1 incident. Rules a future Claude must enforce, not just document:

- **Every query** returning or mutating user-scoped resources MUST filter by `user_id = current_user.id`. Frontend-only filtering is a vulnerability, not a control.
- **No "global" views for non-admin users.** `is_admin = true` is the single exception and its actions must be audit-logged.
- **`ON DELETE RESTRICT`** from projects/agents → users: you can't drop a user with live resources. Soft-disable via `users.is_active = false`. Hard-delete is an explicit, deliberate operation.

**Auth model: JWT in httpOnly cookies, access-token-short + refresh-token-long.**

- **Access token**: JWT, HS256 (RS256 if multi-service later), ~15 min TTL, stateless, contains `user_id`+`exp`+`iat`. Cookie: `httpOnly` + `Secure` (prod) + `SameSite=Lax` + `Path=/`.
- **Refresh token**: opaque random string (NOT a JWT), longer TTL (~14 days), SHA-256-hashed in the `sessions` table. Cookie: same flags + `Path=/api/auth/refresh` (scope narrowed).
- **Never store tokens in `localStorage` or `sessionStorage`.** Reason: XSS exfiltration. httpOnly cookies are unreachable from JS.
- **CSRF protection required** on all `POST`/`PUT`/`PATCH`/`DELETE` — double-submit cookie or synchronizer token. `SameSite=Lax` mitigates but doesn't suffice alone.
- **Password hashing**: argon2id (memory ≥19 MiB, iters ≥2) preferred, bcrypt cost ≥12 fallback. Never plain SHA-256/MD5/plaintext.
- **Logout = revoke session row + clear cookies.** Compromise = revoke session; the 15-min access TTL bounds exposure without rotating the JWT secret.
- **MFA**: TOTP (RFC 6238), recommended for v1, **mandatory** before enabling non-demo real accounts in production. `users.mfa_*` columns are pre-wired.

**Auth provider concrete choice (Auth.js / Clerk / Supabase Auth / custom)**: still open. The `users` table is shaped to be Auth.js-Postgres-adapter compatible (id/email/emailVerified/image columns line up) so the decision doesn't force a migration.

### Data model — `users` and `sessions` tables

Full DDL in `CHARTER.md`. Worth pinning to memory:

- **`users.password_hash` is NULLABLE on purpose** — OAuth users won't have one. Email/password login requires `password_hash IS NOT NULL`. Don't add `NOT NULL` later without thinking about OAuth.
- **`users.email` is enforced lowercase** via `CHECK (email = LOWER(email))`. Normalize at insert; comparisons are byte-exact thereafter.
- **`sessions.refresh_token_hash` stores SHA-256 of the opaque token, never plaintext.** Verification = hash incoming, compare. The access JWT is never persisted.
- **`sessions` uses `ON DELETE CASCADE` from users** (vs RESTRICT elsewhere) — when a user is genuinely deleted, their sessions purge automatically.
- **Anomaly hooks**: `sessions.ip_address` + `user_agent` are inputs for detecting cookie theft (sudden change in active session). Wire alerts to the Auditor or an equivalent backend module.
- **Lockout fields on users** (`failed_login_count`, `locked_until`) implement basic brute-force throttling. Reset to 0 on successful login.

### Data model — `projects` table

The canonical schema for a trading project lives in `CHARTER.md` under "Modelo de Datos: tabla `projects`". Don't restate it here — read it there. Things worth knowing without re-reading the DDL:

- The table uses Postgres features explicitly: `gen_random_uuid()`, `TIMESTAMP`, `TEXT[]`, `JSONB`. **Stack signal: the persistence DB is Postgres.** Don't pick a different DB without flagging it.
- `user_id` ties every project to an owner — auth identity is the gate for visibility and control.
- `status` enum is `active | paused | stopped | error | maintenance`. Only `active` appears on the dashboard main view; the rest are managed under the Proyectos section. **There is no boolean `active` column** — "is this project active?" is always `status = 'active'`. Don't add a duplicate boolean; it will drift.
- The default risk values (`risk_per_trade = 1.0`, `max_daily_dd = 3.0`, `max_total_dd = 8.0`, `max_exposure = 10.0`) are a **floor that satisfies the charter's hard rules** even if the user never touches them. Code that reads these should treat absence-of-override as the safe default, not as "unconfigured."
- `mcp_url` + `mcp_port` are the per-project MT5/MCP endpoint — see container topology below.
- **Broker credentials**: `account_credential_ref` is **always** a pointer into an external secret store. Never store broker passwords as plaintext in this table or any other.
- **Cost columns** (`commission_per_lot`, `swap_long`, `swap_short`, `spread_typical`, `commission_currency`) feed the Auditor's net-of-cost metrics and the real R:R calculation. If the broker doesn't expose one, leave `NULL` and have agents treat it as "unknown," not zero.
- **Per-agent params** are three JSONB columns: `auditor_params`, `investigator_params`, `worker_params`. Schema is owned by each agent and validated at startup. If per-agent config history (e.g. across Sleep Phases) gets dense, promote to a `project_agent_configs` side table — don't bloat the JSONB with embedded history.
- **`trading_sessions TEXT[]`** declares the geographic market sessions in which the Worker is allowed to operate. Allowed values: `sydney`, `shanghai`, `tokyo`, `europe`, `new_york` (enforced via CHECK constraint). Empty array = Worker does not operate. Actual session **clock windows live outside this table** — keep them in backend reference data/constants with DST awareness (US + Europe observe DST; Shanghai doesn't). The Auditor must flag any fill executed outside the union of declared sessions.

### Data model — `agents` table

Three agent definitions per user, each carrying executable Python logic. Full DDL in `CHARTER.md` under "Modelo de Datos: tabla `agents`". Key things to internalize:

- **One row = one reusable agent definition.** `type` ∈ `{worker, investigator, auditor}` — Orchestrator is NOT a row (it's the backend's control plane, not user-defined).
- **`logica TEXT` is the executable body** (Python source). The `runtime` column is CHECK-constrained to `'python'` — there is no DB-level way to set it to MQL5. This is enforcement, not convention.
- **Reused across projects.** A single `agents.id` may be referenced from many `projects` rows via `worker_agent_id` / `investigator_agent_id` / `auditor_agent_id`. Per-project tuning happens in the JSONB `*_params` columns on `projects`, not by duplicating agent rows.
- **`entrypoint`** names the Python function the backend will invoke (e.g. Worker = `on_tick(ctx)`, Investigator = `analyze(ctx)`, Auditor = `evaluate(ctx)`). The contract is owned by the backend, not the agent row.
- **`ON DELETE RESTRICT`** from `projects` → `agents`. To remove an agent, set `is_active = false`. Hard-deletion only when no project still references it.
- **Versioning is single-row (`version` int).** When Sleep Phase generates many revisions of `logica`, promote history to a `agent_versions` side table — don't embed it inside the row.
- **Security gate (NOT in DDL but firm requirement):** executing `agents.logica` is arbitrary code execution. The backend MUST sandbox it — isolated subprocess, no host filesystem, network restricted to that project's MCP endpoint. Capture this when writing the Worker spec; not optional.

### Per-project infrastructure (Docker + MT5)

- **1 project = 1 Docker container = 1 MT5 instance = 1 MCP endpoint.** This 1:1:1:1 isolation is an invariant — no shared containers across projects.
- Base image is `mt5-base:latest` (overridable via `projects.docker_image`). A **default Dockerfile** is parameterized from each project's row (symbol, broker, account, resources).
- The Proyectos section of the dashboard exposes a **button to generate the default Dockerfile** from a project's config. Users should not need to write Docker by hand to bring up a standard project.
- Container lifecycle (`container_id`, `container_name`, `status`) is system-managed and always reflected back into the `projects` row — treat the DB row as source of truth, the Docker daemon as a slave to it.

### Dashboard & UI

- The system's primary operator surface is a **web dashboard**, gated behind authenticated login. No view, metric, position, or control action is reachable without a session — treat auth as a hard precondition, not a feature toggle.
- The dashboard is where the human operator approves gated actions: large/abnormal orders, important post-Sleep-Phase changes, Auditor-proposed emergency halts.
- **Visual theme: GitHub Dark.** Match GitHub's dark-mode palette and chrome — backgrounds in the `#0d1117` / `#161b22` range, light primary text, GitHub-blue accents, subtle borders. When adding UI work, anchor design decisions to this reference rather than inventing a new palette.
- **Main dashboard view shows ONLY `status = 'active'` projects.** Other statuses (paused/stopped/error/maintenance) are managed under the Proyectos sidebar section, not on the main view.
- **Sidebar has exactly four entries, in this order**: `Proyectos`, `Agentes`, `Skills`, `Configuración`. Don't add or rename without explicit user approval — this is product-fixed navigation.
- **Skills are markdown-by-default knowledge artifacts** — prompts, decision frameworks, entry/exit rules. The `python` runtime is reserved for computational/algorithmic skills (indicators, correlation calculators, risk math). Agents reference skills via the `agent_skills` join table (`agent_id`, `skill_id`, optional `notes`, CASCADE from agents, RESTRICT from skills). Multi-tenant integrity: both endpoints must belong to the same `user_id` — enforced at the application layer.

### Behavioral defaults baked into the product

- Capital preservation > profit generation. "Don't trade" beats "trade poorly."
- Every decision includes step-by-step reasoning in its log.
- Maintain live project state: positions, equity, drawdown, metrics, current phase.
- When proposing changes, classify risk level as **Bajo / Medio / Alto** and tag final decisions with `**DECISIÓN FINAL:**`.

## Working language

The charter and product-facing text are in Spanish. Match the existing language in product artifacts (decision tags, response style). Code identifiers and technical docs can stay in English unless the user signals otherwise.

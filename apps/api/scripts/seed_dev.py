"""Dev-only seed script — creates known-credential accounts for local work.

USAGE:
    uv run python scripts/seed_dev.py
    # or via the Makefile target:
    make db.seed

WHAT IT CREATES (all idempotent — re-running is safe):

* alice@example.com  — non-admin user, password: dev_only_change_me_aether_123
* bob@example.com    — admin user,     password: dev_only_change_me_aether_123
* For alice: one demo worker agent, one investigator agent, one auditor agent
  (each with a placeholder `def on_tick(ctx): pass` body).
* For alice: one demo project ``demo-eurusd-h1`` referencing the three agents.
  The project is created with the DB-default ``status='inactive'`` so the
  dashboard's "active projects only" view stays empty until the operator
  explicitly flips it.

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
import sys
from pathlib import Path

# Allow `python scripts/seed_dev.py` from apps/api/ without uv-managed PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.auth.passwords import hash_password
from aether_api.core.settings import get_settings
from aether_api.db.session import get_session_maker
from aether_api.models.agent import Agent
from aether_api.models.project import Project
from aether_api.models.user import User

# -----------------------------------------------------------------------------
# Constants — well-known, dev-only.
# -----------------------------------------------------------------------------
DEV_PASSWORD = "dev_only_change_me_aether_123"  # noqa: S105 — known DEV password

ALICE_EMAIL = "alice@example.com"
BOB_EMAIL = "bob@example.com"

PLACEHOLDER_LOGICA = "def on_tick(ctx):\n    # Placeholder agent logic — replace before any real run.\n    pass\n"

DEMO_AGENTS = [
    ("alice-worker", "worker", "on_tick"),
    ("alice-investigator", "investigator", "analyze"),
    ("alice-auditor", "auditor", "evaluate"),
]

DEMO_PROJECT_NAME = "demo-eurusd-h1"


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
    existing = await session.scalar(
        select(Project).where(Project.user_id == owner.id, Project.name == name)
    )
    if existing is not None:
        return existing, False
    project = Project(
        user_id=owner.id,
        name=name,
        description="Auto-seeded demo project. Inactive by default.",
        symbol="EURUSD",
        timeframe="H1",
        # Leave status at the DB default (`inactive`) so the dashboard
        # empty-state is still demonstrable; users flip it to `active`
        # explicitly in the Proyectos section.
        mcp_url="http://localhost:8081",
        mcp_port=8081,
        worker_agent_id=worker.id,
        investigator_agent_id=investigator.id,
        auditor_agent_id=auditor.id,
    )
    session.add(project)
    await session.flush()
    return project, True


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
        alice, alice_created = await _get_or_create_user(
            session, email=ALICE_EMAIL, password=DEV_PASSWORD, is_admin=False
        )
        bob, bob_created = await _get_or_create_user(
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

        await session.commit()

    print()
    print("Seeded:")
    print(f"  user  alice ({ALICE_EMAIL}) — {'created' if alice_created else 'already present'}")
    print(f"  user  bob   ({BOB_EMAIL})   — {'created' if bob_created else 'already present'} (admin)")
    for name, created in agents_status:
        print(f"  agent {name:<24} — {'created' if created else 'already present'}")
    print(f"  project {DEMO_PROJECT_NAME:<22} — {'created' if project_created else 'already present'} (status=inactive)")
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

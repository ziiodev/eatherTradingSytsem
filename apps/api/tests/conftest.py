"""Pytest fixtures for the Aether backend test suite.

The fixtures fall into two layers:

* **Schema layer** — :func:`database_url` boots an ephemeral Postgres
  via testcontainers (or reuses ``DATABASE_URL`` when set) and runs
  Alembic up to ``head``.
* **App layer** — :func:`app_client` mounts the FastAPI app onto an
  ``httpx.AsyncClient`` driven through ``asgi-lifespan`` so startup/
  shutdown handlers run normally.

Tests marked ``integration`` are skipped automatically when neither a
``DATABASE_URL`` env var nor Docker is available.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Final

import pytest

#: Directory containing alembic.ini (one level up from tests/).
API_ROOT: Final[Path] = Path(__file__).resolve().parent.parent


def _async_url_from_sync(sync_url: str) -> str:
    """Convert a psycopg / plain postgres URL into an asyncpg URL."""
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://"):
        if sync_url.startswith(prefix):
            return "postgresql+asyncpg://" + sync_url[len(prefix):]
    return sync_url


# ---------------------------------------------------------------------------
# Database URL — ephemeral Postgres or DATABASE_URL passthrough.
# ---------------------------------------------------------------------------


def _is_dev_database(url: str) -> bool:
    """Return True iff ``url`` points at the dev database (name='aether').

    Conservative heuristic: parse the path component and check the final
    segment. SQLAlchemy URLs and PostgreSQL DSNs share the structure
    ``scheme://user:pass@host:port/dbname?...``.
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        # `path` is `/dbname` (or `/dbname?...` once query is stripped).
        db_name = parsed.path.lstrip("/").split("?", 1)[0]
        return db_name == "aether"
    except Exception:
        # Be lenient: if we can't parse, don't block.
        return False

@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Provide an async DB URL for migration / integration tests.

    Priority order:
      1. ``TEST_DATABASE_URL`` (explicit opt-in for an isolated test DB).
      2. ``DATABASE_URL`` (fallback for environments without a dedicated
         test DB — e.g. CI containers that spin one up per job).
      3. testcontainers Postgres (ephemeral, requires Docker).

    GUARD: if the resolved URL points at a DB whose name is ``aether``
    (the canonical dev DB), refuse to run. The autouse
    ``_truncate_mutable_tables`` fixture would wipe alice/bob/seed data
    on every test — that's a footgun. Create ``aether_test`` and set
    ``TEST_DATABASE_URL`` instead. The error spells out the fix.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    fallback_url = os.environ.get("DATABASE_URL")
    existing = test_url or fallback_url
    if existing:
        # Refuse the dev DB unless the caller is explicit about it.
        if _is_dev_database(existing) and test_url is None:
            pytest.fail(
                "\n\n"
                "Refusing to run tests against the dev database (name='aether').\n"
                "Tests would TRUNCATE users/sessions/agents/projects/skills/...\n"
                "wiping your seeded data on every run.\n\n"
                "Fix: create a separate DB and set TEST_DATABASE_URL.\n"
                "    docker compose exec postgres \\\n"
                "      psql -U aether -d postgres \\\n"
                "      -c 'CREATE DATABASE aether_test OWNER aether;'\n"
                "    export TEST_DATABASE_URL="
                "postgresql+asyncpg://aether:dev_only_change_me@localhost:5435/aether_test\n"
                "    cd apps/api && DATABASE_URL=\"$TEST_DATABASE_URL\" "
                "uv run alembic upgrade head\n"
                "    # then re-run pytest in this shell.\n",
                pytrace=False,
            )
        # Trust the caller — they configured the URL deliberately.
        os.environ["DATABASE_URL"] = existing
        yield existing
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed and DATABASE_URL/TEST_DATABASE_URL not set")
        return

    try:
        container = PostgresContainer(
            "postgres:16-alpine",
            username="aether",
            password="aether",
            dbname="aether",
        )
        container.start()
    except Exception as exc:  # pragma: no cover — Docker not available
        pytest.skip(f"Could not start testcontainers Postgres (Docker unavailable?): {exc}")
        return

    try:
        sync_url = container.get_connection_url()
        async_url = _async_url_from_sync(sync_url)
        os.environ["DATABASE_URL"] = async_url
        yield async_url
    finally:
        os.environ.pop("DATABASE_URL", None)
        container.stop()


# ---------------------------------------------------------------------------
# Settings env — JWT secret + RS256 keypair, must be set before importing
# aether_api.main. The settings model requires a valid RS256 keypair at boot
# (see rs256-jwt-migration); we generate an ephemeral one for the test session
# so individual tests don't need to manage key material themselves.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _settings_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Populate the minimal env so :func:`get_settings` succeeds at import."""
    set_jwt = False
    if "JWT_SECRET" not in os.environ:
        os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
        set_jwt = True
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("COOKIE_SECURE", "false")

    # MFA secrets — required by the mfa-totp change. The settings
    # validators reject malformed values, so we generate well-shaped
    # ones (32 random bytes URL-safe-base64 for the Fernet key, 64 hex
    # chars for the HS256 pending secret).
    set_mfa_secret_key = "MFA_SECRET_KEY" not in os.environ
    set_mfa_pending_secret = "MFA_PENDING_SECRET" not in os.environ
    if set_mfa_secret_key:
        import base64 as _b64

        os.environ["MFA_SECRET_KEY"] = _b64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode("ascii")
    if set_mfa_pending_secret:
        os.environ["MFA_PENDING_SECRET"] = secrets.token_hex(32)

    # Synthesize a 2048-bit RSA keypair into a temp dir and point the settings
    # at it via the path-based env vars. We only do this when neither inline
    # PEM nor path is already set — CI / contributors with their own dev keys
    # can override by exporting JWT_PRIVATE_KEY_PATH before running pytest.
    set_keys = (
        "JWT_PRIVATE_KEY_PEM" not in os.environ
        and "JWT_PRIVATE_KEY_PATH" not in os.environ
        and "JWT_PUBLIC_KEY_PEM" not in os.environ
        and "JWT_PUBLIC_KEY_PATH" not in os.environ
    )
    if set_keys:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key_dir = tmp_path_factory.mktemp("rs256_keys")
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_path = key_dir / "private.pem"
        pub_path = key_dir / "public.pem"
        priv_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        pub_path.write_bytes(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        os.environ["JWT_PRIVATE_KEY_PATH"] = str(priv_path)
        os.environ["JWT_PUBLIC_KEY_PATH"] = str(pub_path)

    try:
        yield
    finally:
        if set_jwt:
            os.environ.pop("JWT_SECRET", None)
        if set_keys:
            os.environ.pop("JWT_PRIVATE_KEY_PATH", None)
            os.environ.pop("JWT_PUBLIC_KEY_PATH", None)
        if set_mfa_secret_key:
            os.environ.pop("MFA_SECRET_KEY", None)
        if set_mfa_pending_secret:
            os.environ.pop("MFA_PENDING_SECRET", None)


# ---------------------------------------------------------------------------
# Alembic — runs migrations against the live URL.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def alembic_config(database_url: str):
    """Build an Alembic ``Config`` rooted at ``apps/api/alembic.ini``."""
    from alembic.config import Config

    ini_path = API_ROOT / "alembic.ini"
    if not ini_path.exists():
        pytest.skip(f"alembic.ini not found at {ini_path}")

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    assert os.environ.get("DATABASE_URL") == database_url
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_config, database_url: str) -> Iterator[str]:
    """Run ``alembic upgrade head`` then yield the URL. Reset on teardown."""
    from alembic import command

    # Use "heads" (plural) — the Wave 3 parallel changes (mfa-totp +
    # docker-orchestration) both chained off 0004_agent_runs, leaving
    # two siblings until the orchestrator stitches a merge migration.
    command.upgrade(alembic_config, "heads")
    try:
        yield database_url
    finally:
        # Best-effort downgrade — if it fails (e.g. data dependencies),
        # the testcontainer cleanup nukes the DB anyway.
        with contextlib.suppress(Exception):
            command.downgrade(alembic_config, "base")


# ---------------------------------------------------------------------------
# Per-test DB cleanup
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
async def _truncate_mutable_tables(migrated_db: str) -> AsyncIterator[None]:
    """Wipe charter tables between tests so fixtures can re-seed deterministically.

    The migration is session-scoped (cheap) but row state must reset per test
    — otherwise tests that seed the same email (e.g. cross-tenant tests
    using ``a@example.com``) collide with each other and fail spuriously on
    the unique-email constraint. ``TRUNCATE ... CASCADE`` is the cleanest
    way to nuke users/sessions/agents/projects in one shot while respecting
    foreign keys.
    """
    from aether_api.db.session import get_engine
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE users, sessions, agents, projects, skills, "
                "agent_skills, agent_runs, mfa_recovery_codes, "
                "sleep_runs, sleep_reflections, config_versions, "
                "q_tables, episodic_memory, semantic_memory, sleep_reports "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


# ---------------------------------------------------------------------------
# App + HTTP client
# ---------------------------------------------------------------------------
@pytest.fixture
async def app_client(migrated_db: str) -> AsyncIterator:
    """Yield an httpx.AsyncClient bound to a freshly-imported FastAPI app.

    asgi-lifespan ensures startup/shutdown hooks run, so
    :func:`setup_logging` and similar fire just like in production.
    """
    try:
        import httpx
        from asgi_lifespan import LifespanManager
    except ImportError:
        pytest.skip("asgi-lifespan / httpx not installed")
        return

    # Import lazily — env vars MUST already be set when settings load.
    from aether_api.core.settings import get_settings
    from aether_api.main import create_app

    get_settings.cache_clear()
    app = create_app()

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


# ---------------------------------------------------------------------------
# Event loop (for session-scoped async fixtures on older pytest-asyncio).
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()

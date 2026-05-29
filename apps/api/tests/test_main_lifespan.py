"""Integration tests for the FastAPI lifespan — chat sweeper wiring.

The lifespan hook (in :mod:`aether_api.main`) must:

1. When ``settings.chat_enabled`` AND ``settings.anthropic_api_key`` are
   both set → spawn the chat aborted-sweeper background task and
   attach the handle to ``app.state.chat_sweeper_task``.
2. When ``settings.chat_enabled`` is False → not start the sweeper
   AND not mount the chat router (POST to a chat URL → 404).
3. When ``settings.chat_enabled`` is True but the Anthropic API key is
   absent → the router IS mounted (frontend gating via /api/health)
   but the sweeper is NOT started (no orphans can exist without
   live turns).
4. On shutdown → the sweeper task is cancelled cleanly with no leaked
   asyncio tasks.

These tests must not point at the dev DB. They lean on the existing
``migrated_db`` fixture from ``conftest.py`` which boots an ephemeral
testcontainers Postgres (or honours TEST_DATABASE_URL).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enable_chat_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env so settings produce chat_enabled=True AND a valid api_key."""
    monkeypatch.setenv("AETHER_CHAT_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")


def _disable_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_CHAT_ENABLED", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _enable_chat_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_CHAT_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_lifespan_starts_chat_sweeper_when_enabled(
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_enabled=True + api_key set → sweeper task on app.state."""
    import httpx
    from aether_api.core.settings import get_settings
    from aether_api.main import create_app
    from asgi_lifespan import LifespanManager

    _enable_chat_with_key(monkeypatch)
    get_settings.cache_clear()
    assert get_settings().chat_enabled is True
    assert get_settings().anthropic_api_key is not None

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200, health.text

            task: asyncio.Task[Any] | None = getattr(
                app.state, "chat_sweeper_task", None
            )
            assert task is not None, "sweeper task must be attached"
            assert not task.done(), "sweeper must still be running"
            assert task.get_name() == "chat-aborted-sweeper"

    # After shutdown the task must be cancelled (or otherwise resolved).
    assert task is not None
    assert task.done(), "sweeper must be resolved after shutdown"


async def test_lifespan_skips_sweeper_and_router_when_chat_disabled(
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_enabled=False → no sweeper, no chat router mounted (404)."""
    import uuid as _uuid

    import httpx
    from aether_api.core.settings import get_settings
    from aether_api.main import create_app
    from asgi_lifespan import LifespanManager

    _disable_chat(monkeypatch)
    get_settings.cache_clear()
    assert get_settings().chat_enabled is False

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200

            # Sweeper task is the None sentinel.
            assert getattr(app.state, "chat_sweeper_task", "missing") is None

            # Chat endpoints aren't mounted — POST returns 404 regardless
            # of body / auth (router doesn't even register the path).
            random_pid = _uuid.uuid4()
            random_cid = _uuid.uuid4()
            resp = await client.post(
                f"/api/projects/{random_pid}/chat/conversations/{random_cid}/messages",
                json={"content": "noop"},
            )
            assert resp.status_code == 404, resp.text


async def test_lifespan_skips_sweeper_when_key_absent_but_flag_on(
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat_enabled=True without API key → router mounted, sweeper NOT started.

    The router still mounts so the frontend's feature-flag query
    (``GET /api/health``) reflects ``chat_enabled = flag AND bool(key)``
    accurately. The router itself raises 500 ``CHAT_NOT_CONFIGURED``
    on POSTs — already covered in tests/routers/test_chat.py. We only
    check the sweeper handle here.
    """
    import httpx
    from aether_api.core.settings import get_settings
    from aether_api.main import create_app
    from asgi_lifespan import LifespanManager

    _enable_chat_without_key(monkeypatch)
    get_settings.cache_clear()
    assert get_settings().chat_enabled is True
    assert get_settings().anthropic_api_key is None

    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            health = await client.get("/healthz")
            assert health.status_code == 200

            # No sweeper — the start condition requires BOTH flag and key.
            assert getattr(app.state, "chat_sweeper_task", "missing") is None

            # /api/health surfaces the AND-ed feature flag — chat_enabled
            # is False from the frontend's perspective because the key
            # is missing.
            api_health = await client.get("/api/health")
            assert api_health.status_code == 200
            features = api_health.json()["features"]
            assert features["chat_enabled"] is False


async def test_lifespan_shutdown_cancels_sweeper_cleanly(
    migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown must cancel the sweeper task without leaking it."""
    import httpx
    from aether_api.core.settings import get_settings
    from aether_api.main import create_app
    from asgi_lifespan import LifespanManager

    _enable_chat_with_key(monkeypatch)
    get_settings.cache_clear()

    app = create_app()
    captured_task: asyncio.Task[Any] | None = None

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.get("/healthz")
            captured_task = app.state.chat_sweeper_task
            assert captured_task is not None
            assert not captured_task.done()

    # Outside the LifespanManager → shutdown finished. The task must
    # have resolved (either cancelled or otherwise completed). No
    # pending exception is acceptable since the sweeper catches its own.
    assert captured_task is not None
    assert captured_task.done(), "sweeper must be resolved after shutdown"
    # The task must NOT be lingering in the running loop.
    pending = {
        t
        for t in asyncio.all_tasks()
        if t.get_name() == "chat-aborted-sweeper" and not t.done()
    }
    assert not pending, f"sweeper leaked across shutdown: {pending!r}"

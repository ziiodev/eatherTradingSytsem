"""Body-size guard middleware for /api/agents POST/PATCH.

We don't bother seeding a user — the middleware fires BEFORE the auth
dependency runs (it inspects ``Content-Length`` only). A 413 here proves
the perimeter is in place; the auth checks are exercised elsewhere.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_oversize_post_returns_413_before_auth(app_client) -> None:
    # Import lazily so settings (which require DATABASE_URL) are only
    # built when fixtures have already wired the env.
    from aether_api.main import AGENT_WRITE_BODY_LIMIT_BYTES

    # A single huge field is enough; Content-Length is what the guard reads.
    huge = "a" * (AGENT_WRITE_BODY_LIMIT_BYTES + 100)
    resp = await app_client.post(
        "/api/agents",
        json={
            "name": "x",
            "type": "worker",
            "logica": huge,
        },
    )
    assert resp.status_code == 413, resp.text
    body = resp.json()
    assert body["detail"] == "request body too large"
    assert body["max_bytes"] == AGENT_WRITE_BODY_LIMIT_BYTES


async def test_within_budget_post_does_not_413(app_client) -> None:
    """A tiny payload passes the guard — even though auth/CSRF will then fail."""
    resp = await app_client.post(
        "/api/agents",
        json={"name": "n", "type": "worker", "logica": "def on_tick(ctx): pass\n"},
    )
    # We expect 401 (no auth cookie) — NOT 413.
    assert resp.status_code != 413

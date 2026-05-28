"""Markdown-default skill creation + Python runtime regression.

Covers:

* POST without ``runtime`` defaults to ``'markdown'``.
* Empty markdown body → 422 (min_length=1 at the Pydantic layer).
* Markdown body containing garbage that LOOKS like broken Python is
  accepted as 201 — markdown is permissive.
* POST with ``runtime='python'`` + bad syntax → 422 with
  ``python_syntax_error`` (renamed from ``code_syntax_error``).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


CODE_VALID = "def rsi(series, period=14):\n    return series\n"
CODE_BROKEN = "def rsi(series, period=14)\n    return series\n"

MD_BODY = "# Entry rule\n\n- buy when RSI < 30\n- sell when RSI > 70\n"


async def _seed_and_login(client, email: str = "md-owner@example.com") -> str:
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password="testtesttesttest")
        await session.commit()
        user_id = str(user.id)

    resp = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "testtesttesttest"},
    )
    assert resp.status_code == 200, resp.text
    return user_id


def _csrf_headers(client) -> dict[str, str]:
    from aether_api.auth.cookies import CSRF_COOKIE

    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — login was not run first"
    return {"X-CSRF-Token": token}


# ---------------------------------------------------------------------------
# Default runtime
# ---------------------------------------------------------------------------


async def test_post_without_runtime_defaults_to_markdown(app_client) -> None:
    """Omitting ``runtime`` on POST defaults the field to 'markdown'."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={"name": "entry-rule", "type": "analytic", "code": MD_BODY},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["runtime"] == "markdown"
    assert body["code"] == MD_BODY
    assert body["used_by_agent_count"] == 0


async def test_markdown_default_accepts_python_looking_garbage(app_client) -> None:
    """A markdown skill body that LOOKS like broken Python is fine.

    Markdown is freeform prose — we don't try to parse it. Anything that
    is non-empty and under the size cap goes through.
    """
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "rough-notes",
            "type": "analytic",
            "code": CODE_BROKEN,  # python-looking but invalid
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["runtime"] == "markdown"


async def test_markdown_empty_code_is_422(app_client) -> None:
    """``Field(min_length=1)`` on ``code`` rejects an empty body."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={"name": "blank", "type": "analytic", "code": ""},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Python runtime — broken syntax now returns python_syntax_error
# ---------------------------------------------------------------------------


async def test_python_runtime_bad_syntax_is_422(app_client) -> None:
    """Explicit ``runtime='python'`` + broken source → 422 with renamed code."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "broken-rsi",
            "type": "indicator",
            "runtime": "python",
            "code": CODE_BROKEN,
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "python_syntax_error"
    assert detail["line"] == 1


async def test_python_runtime_valid_source_is_201(app_client) -> None:
    """Valid Python source under runtime='python' creates the skill."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "rsi-14",
            "type": "indicator",
            "runtime": "python",
            "code": CODE_VALID,
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["runtime"] == "python"
    assert body["code"] == CODE_VALID


# ---------------------------------------------------------------------------
# Invalid runtime → 422 from Pydantic
# ---------------------------------------------------------------------------


async def test_post_invalid_runtime_is_422(app_client) -> None:
    """Anything outside {markdown, python} is rejected at the Pydantic layer."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/skills",
        json={
            "name": "n",
            "type": "analytic",
            "runtime": "mql5",
            "code": MD_BODY,
        },
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 422

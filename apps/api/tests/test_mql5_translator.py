"""Integration tests for ``POST /api/tools/mql5-to-python``.

Cover the gate matrix end-to-end:

* Feature flag off → 503.
* Body over the byte cap → 413.
* API key missing while flag is on → 503.
* Happy path (Anthropic SDK stubbed) → 200 with expected envelope.
* Audit log row written with SIZE only (no MQL5 / Python content).

The Anthropic SDK is monkey-patched to a fake ``Anthropic`` class so
the test runs offline and deterministically. The real SDK is only ever
imported lazily inside :mod:`aether_api.services.mql5_translator` so
the stub takes effect before the first call.
"""

from __future__ import annotations

import os
import sys
import types
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration

MQL5_SOURCE = (
    "// Trivial EA — opens a market buy when RSI crosses up.\n"
    "void OnTick() {\n"
    "    double rsi = iRSI(NULL, 0, 14, PRICE_CLOSE, 0);\n"
    "    if (rsi < 30) {\n"
    "        OrderSend(Symbol(), OP_BUY, 0.1, Ask, 3, Ask-200*Point, 0, NULL);\n"
    "    }\n"
    "}\n"
)

CANNED_PYTHON = (
    "# TODO: review — auto-translated from MQL5.\n"
    "# Re-check entrypoint, risk parameters, and MCP tool names before saving.\n"
    "def on_tick(ctx):\n"
    "    rsi = ctx.skills['rsi'](ctx.candles, period=14)\n"
    "    if rsi < 30:\n"
    "        ctx.mcp.place_order(\n"
    "            ctx.symbol, 'buy', 0.1,\n"
    "            sl=ctx.tick.ask - 200 * ctx.point,\n"
    "        )\n"
    "    return None\n"
)


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — injected into sys.modules before the service is imported.
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, input_tokens: int, output_tokens: int) -> None:
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _FakeMessages:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    def create(self, **kwargs: Any) -> _FakeResponse:
        self._calls.append(kwargs)
        # Return canned Python unconditionally; tests that need failure
        # paths swap the class out.
        return _FakeResponse(CANNED_PYTHON, input_tokens=42, output_tokens=128)


class _FakeAnthropic:
    """Drop-in for ``anthropic.Anthropic`` — records calls in-class."""

    last_init_kwargs: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        _FakeAnthropic.last_init_kwargs = kwargs
        self.messages = _FakeMessages(_FakeAnthropic.calls)


def _install_fake_anthropic() -> None:
    """Inject the fake module into ``sys.modules`` so the lazy import picks it up."""
    fake = types.ModuleType("anthropic")
    fake.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake
    # Reset per-test call log + init kwargs.
    _FakeAnthropic.calls.clear()
    _FakeAnthropic.last_init_kwargs = {}


def _install_failing_anthropic() -> None:
    """Inject a fake whose ``create`` raises — drives the 502 branch."""

    class _Failing:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            raise RuntimeError("simulated upstream blowup")

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Failing  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake


# ---------------------------------------------------------------------------
# Auth helpers (mirror test_agents_crud.py)
# ---------------------------------------------------------------------------


async def _seed_and_login(client, email: str = "translator@example.com") -> str:
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
# Settings helpers
# ---------------------------------------------------------------------------


def _set_env(name: str, value: str | None) -> tuple[str, str | None]:
    """Set / clear an env var and return ``(name, prior)`` for cleanup."""
    prior = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    return name, prior


def _restore_env(saved: list[tuple[str, str | None]]) -> None:
    for name, prior in saved:
        if prior is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prior


# ---------------------------------------------------------------------------
# Gate matrix
# ---------------------------------------------------------------------------


async def test_translator_disabled_returns_503(app_client) -> None:
    """Feature flag off (default) → 503 with the stable detail string."""
    await _seed_and_login(app_client)
    resp = await app_client.post(
        "/api/tools/mql5-to-python",
        json={"mql5": MQL5_SOURCE},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "translator not enabled"


async def test_body_over_cap_returns_413(app_client) -> None:
    """50 KiB + 1 byte → 413 before any upstream call is attempted."""
    from aether_api.core.settings import get_settings

    saved = [
        _set_env("MQL5_TRANSLATOR_ENABLED", "true"),
        _set_env("ANTHROPIC_API_KEY", "test-key"),
        _set_env("MQL5_TRANSLATOR_MAX_INPUT_BYTES", "1024"),
    ]
    get_settings.cache_clear()
    _install_fake_anthropic()
    try:
        await _seed_and_login(app_client)
        big = "// fluff\n" * 2000  # ~16 KiB > 1 KiB cap
        resp = await app_client.post(
            "/api/tools/mql5-to-python",
            json={"mql5": big},
            headers=_csrf_headers(app_client),
        )
        assert resp.status_code == 413, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "mql5_too_large"
        assert body["detail"]["max_bytes"] == 1024
        # Crucially: no upstream call was made.
        assert _FakeAnthropic.calls == []
    finally:
        _restore_env(saved)
        get_settings.cache_clear()


async def test_missing_api_key_returns_503(app_client) -> None:
    """Flag on, API key unset → 503 ``translator not configured``."""
    from aether_api.core.settings import get_settings

    saved = [
        _set_env("MQL5_TRANSLATOR_ENABLED", "true"),
        _set_env("ANTHROPIC_API_KEY", None),
    ]
    get_settings.cache_clear()
    # Even without a key set, the lazy import never fires because the
    # service raises ``TranslatorNotConfiguredError`` first. We still
    # install the fake to guard against accidental real calls.
    _install_fake_anthropic()
    try:
        await _seed_and_login(app_client)
        resp = await app_client.post(
            "/api/tools/mql5-to-python",
            json={"mql5": MQL5_SOURCE},
            headers=_csrf_headers(app_client),
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == "translator not configured"
        assert _FakeAnthropic.calls == []
    finally:
        _restore_env(saved)
        get_settings.cache_clear()


async def test_happy_path_returns_200_with_canned_python(app_client) -> None:
    """Happy path: flag on, key set, fake SDK returns canned Python."""
    from aether_api.core.settings import get_settings

    saved = [
        _set_env("MQL5_TRANSLATOR_ENABLED", "true"),
        _set_env("ANTHROPIC_API_KEY", "test-key"),
        _set_env("MQL5_TRANSLATOR_MODEL", "claude-test-model"),
    ]
    get_settings.cache_clear()
    _install_fake_anthropic()
    try:
        await _seed_and_login(app_client)
        resp = await app_client.post(
            "/api/tools/mql5-to-python",
            json={"mql5": MQL5_SOURCE, "target_entrypoint": "on_tick"},
            headers=_csrf_headers(app_client),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["python"] == CANNED_PYTHON
        assert body["model"] == "claude-test-model"
        assert body["input_tokens"] == 42
        assert body["output_tokens"] == 128

        # The SDK was called with the system prompt baked in + the user
        # message containing the entrypoint hint.
        assert len(_FakeAnthropic.calls) == 1
        call = _FakeAnthropic.calls[0]
        assert call["model"] == "claude-test-model"
        assert "Target Python entrypoint: on_tick" in call["messages"][0]["content"]
        # Critical safety check: the system prompt forbids direct mt5 calls.
        assert "ctx.mcp.place_order" in call["system"]
        assert _FakeAnthropic.last_init_kwargs["api_key"] == "test-key"
    finally:
        _restore_env(saved)
        get_settings.cache_clear()


async def test_upstream_error_returns_502_with_stable_code(app_client) -> None:
    """When the SDK raises, the endpoint surfaces 502 + stable error code."""
    from aether_api.core.settings import get_settings

    saved = [
        _set_env("MQL5_TRANSLATOR_ENABLED", "true"),
        _set_env("ANTHROPIC_API_KEY", "test-key"),
    ]
    get_settings.cache_clear()
    _install_failing_anthropic()
    try:
        await _seed_and_login(app_client)
        resp = await app_client.post(
            "/api/tools/mql5-to-python",
            json={"mql5": MQL5_SOURCE},
            headers=_csrf_headers(app_client),
        )
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "translator_upstream_error"
    finally:
        _restore_env(saved)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Audit log invariant — sizes only, never content
# ---------------------------------------------------------------------------


async def test_audit_log_records_sizes_only(app_client) -> None:
    """On success the audit_log row carries sizes/model/tokens — not MQL5/Python."""
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.audit_repository import AuditRepository

    saved = [
        _set_env("MQL5_TRANSLATOR_ENABLED", "true"),
        _set_env("ANTHROPIC_API_KEY", "test-key"),
        _set_env("MQL5_TRANSLATOR_MODEL", "claude-test-model"),
        _set_env("AUDIT_LOG_ENABLED", "true"),
    ]
    get_settings.cache_clear()
    _install_fake_anthropic()
    try:
        user_id_str = await _seed_and_login(app_client)
        user_id = uuid.UUID(user_id_str)

        resp = await app_client.post(
            "/api/tools/mql5-to-python",
            json={"mql5": MQL5_SOURCE, "target_entrypoint": "on_tick"},
            headers=_csrf_headers(app_client),
        )
        assert resp.status_code == 200, resp.text

        maker = get_session_maker()
        async with maker() as session:
            repo = AuditRepository(session)
            rows = await repo.list_for_user(user_id)

        translate_rows = [r for r in rows if r.action == "mql5_translate"]
        assert len(translate_rows) == 1, [r.action for r in rows]
        row = translate_rows[0]
        assert row.target_type == "tools"
        assert row.target_id is None

        # Sizes only — never raw content.
        assert row.before_state is not None
        assert row.before_state["mql5_size"] == len(MQL5_SOURCE.encode("utf-8"))
        assert row.before_state["target_entrypoint"] == "on_tick"
        assert "mql5" not in row.before_state  # raw input must NEVER appear

        assert row.after_state is not None
        assert row.after_state["python_size"] == len(CANNED_PYTHON.encode("utf-8"))
        assert row.after_state["model"] == "claude-test-model"
        assert row.after_state["input_tokens"] == 42
        assert row.after_state["output_tokens"] == 128
        assert "python" not in row.after_state  # raw output must NEVER appear
    finally:
        _restore_env(saved)
        get_settings.cache_clear()

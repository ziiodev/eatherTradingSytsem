"""Phase 11 of ``sdd/sleep-learning-loop`` — formal ``learning_enabled``
flag.

Pinned behavioural contract:

* ``AETHER_LEARNING_ENABLED`` env var → mapped onto
  :attr:`aether_api.core.settings.Settings.learning_enabled` by
  pydantic-settings.
* When the flag is False:

  * :func:`aether_api.sleep.learning_step.is_learning_enabled` returns
    ``False`` and the orchestrator skips the deep-sleep learning step.
  * :func:`aether_api.sandbox.engine._learning_enabled_from_env` returns
    ``False`` and the sandbox binds the Noop learning proxies on the
    child's ``ctx``.

* When the flag is True (any of ``true`` / ``1`` / ``yes`` / ``on``,
  case-insensitive — pydantic's standard boolean parser):

  * Both helpers return ``True`` and the gated code paths run.

These tests don't drive the orchestrator end-to-end (the Phase 7
integration suite already pins that); they pin the **single-source-of-
truth contract**: every callsite reads through ``Settings.learning_enabled``
and nothing else.
"""

from __future__ import annotations

import importlib

import pytest


def _clear_settings_cache() -> None:
    """Drop the lru_cache wrapping :func:`get_settings`.

    Tests mutate the env at runtime; without this the next call returns
    the pre-mutation snapshot.
    """
    from aether_api.core.settings import get_settings

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# (a) Default — flag absent → all three helpers report disabled.
# ---------------------------------------------------------------------------


def test_default_flag_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence of ``AETHER_LEARNING_ENABLED`` ⇒ ``learning_enabled=False``."""
    monkeypatch.delenv("AETHER_LEARNING_ENABLED", raising=False)
    _clear_settings_cache()

    from aether_api.core.settings import get_settings
    from aether_api.sandbox.engine import _learning_enabled_from_env
    from aether_api.sleep.learning_step import is_learning_enabled

    assert get_settings().learning_enabled is False
    assert is_learning_enabled() is False
    assert _learning_enabled_from_env() is False


# ---------------------------------------------------------------------------
# (b) Truthy env values flip the flag on.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["true", "True", "TRUE", "1", "yes", "on"])
def test_truthy_env_enables_flag(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """pydantic-settings parses every standard truthy spelling identically."""
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", raw)
    _clear_settings_cache()

    from aether_api.core.settings import get_settings
    from aether_api.sandbox.engine import _learning_enabled_from_env
    from aether_api.sleep.learning_step import is_learning_enabled

    assert get_settings().learning_enabled is True
    assert is_learning_enabled() is True
    assert _learning_enabled_from_env() is True


# ---------------------------------------------------------------------------
# (c) Falsy env values keep the flag off.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["false", "0", "no", "off"])
def test_falsy_env_keeps_flag_off(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", raw)
    _clear_settings_cache()

    from aether_api.core.settings import get_settings
    from aether_api.sandbox.engine import _learning_enabled_from_env
    from aether_api.sleep.learning_step import is_learning_enabled

    assert get_settings().learning_enabled is False
    assert is_learning_enabled() is False
    assert _learning_enabled_from_env() is False


# ---------------------------------------------------------------------------
# (d) Sandbox _build_ctx threads the flag value onto AgentContext.
# ---------------------------------------------------------------------------


def test_build_ctx_propagates_learning_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_build_ctx`` reads the flag once per spawn — when off, the
    resulting :class:`AgentContext` carries ``learning_enabled=False``."""
    monkeypatch.delenv("AETHER_LEARNING_ENABLED", raising=False)
    _clear_settings_cache()

    import uuid
    from types import SimpleNamespace

    from aether_api.sandbox.engine import _build_ctx

    agent_row = SimpleNamespace(
        id=uuid.uuid4(),
        type="worker",
        entrypoint="on_tick",
        logica="def on_tick(ctx): return None\n",
    )
    project_row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        symbol="EURUSD",
        timeframe="H1",
        mcp_url="http://127.0.0.1:65000",
        mcp_port=65000,
    )
    ctx = _build_ctx(
        agent_row=agent_row,
        project_row=project_row,
        inputs={},
        dry_run=True,
        mode="manual",
    )
    assert ctx.learning_enabled is False


def test_build_ctx_propagates_learning_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", "true")
    _clear_settings_cache()

    import uuid
    from types import SimpleNamespace

    from aether_api.sandbox.engine import _build_ctx

    agent_row = SimpleNamespace(
        id=uuid.uuid4(),
        type="worker",
        entrypoint="on_tick",
        logica="def on_tick(ctx): return None\n",
    )
    project_row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        symbol="EURUSD",
        timeframe="H1",
        mcp_url="http://127.0.0.1:65000",
        mcp_port=65000,
    )
    ctx = _build_ctx(
        agent_row=agent_row,
        project_row=project_row,
        inputs={},
        dry_run=True,
        mode="manual",
    )
    assert ctx.learning_enabled is True


# ---------------------------------------------------------------------------
# (e) Lifespan warm pass — gated on settings.learning_enabled.
# ---------------------------------------------------------------------------


def test_lifespan_skip_marker_module_scope() -> None:
    """The lifespan sentinel exception lives at module scope so the
    ``except`` clause that swallows it never accidentally catches a
    learning-recovery bug. Pin the location so a refactor that moves it
    breaks here loudly, not silently."""
    main_mod = importlib.import_module("aether_api.main")
    assert hasattr(main_mod, "_LearningWarmSkipped")
    assert issubclass(main_mod._LearningWarmSkipped, Exception)

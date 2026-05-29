"""Sandbox learning-proxy escape-attempt + integration suite.

Every test in this module spawns a REAL subprocess via
:class:`aether_api.sandbox.engine.Engine` — no mocking of the spawn /
pipe / RPC machinery. The canonical agent-sandbox spec (#2046) requires
that the security-critical path be exercised end-to-end.

Coverage:

(a) ``ctx.qtable.set(...)`` → AttributeError (no setter exists).
(b) Episodic delete via any vector → impossible (proxy exposes only
    ``.record``).
(c) Cross-project semantic read — agent mutates ``ctx.semantic.project_id``;
    proxy is frozen, AttributeError; AND the parent-side handler uses the
    bound user_id+project_id (defence in depth).
(d) Episodic ``user_id`` tampering — proxy is frozen, AttributeError; AND
    the parent-side dispatcher strips any user_id/project_id from the
    child's payload and uses its bound tuple.

Plus an integration test: a sandbox subprocess runs agent code that
calls ``ctx.episodic.record(...)``; the parent observes the call with
the bound ``(user_id, project_id)`` recorded in a FakeRepo.

The parent-side handlers are stubbed via the ``rpc_handlers`` Engine
constructor knob — this lets us drive the boundary without a live
Postgres connection. The escape vectors we're testing are CHILD-side
(frozen-dataclass tamper, missing setter) and DISPATCHER-side (the
dispatcher uses its OWN bound IDs), neither of which needs the real DB.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from aether_api.sandbox.engine import Engine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(*, user_id: uuid.UUID | None = None) -> Any:
    """SimpleNamespace stand-in for a Project ORM row.

    The engine reads only ``id``, ``user_id``, ``symbol``, ``timeframe``,
    ``mcp_url``, ``mcp_port`` — so a SimpleNamespace satisfies the surface
    without touching SQLAlchemy.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        symbol="EURUSD",
        timeframe="H1",
        mcp_url="http://127.0.0.1:65000",
        mcp_port=65000,
    )


def _make_agent(*, logica: str, entrypoint: str = "on_tick") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        logica=logica,
        entrypoint=entrypoint,
        type="worker",
    )


def _engine_with_handlers(handlers: dict[str, Any] | None = None) -> Engine:
    """Build an Engine wired with stub handlers + a tight wall clock.

    Passing ``rpc_handlers=`` skips :func:`build_default_handlers` (which
    would otherwise import the learning repos and need a real session).
    """
    return Engine(
        wall_clock_seconds=8.0,
        rlimit_cpu_seconds=5,
        rlimit_as_bytes=256 * 1024 * 1024,
        rlimit_nofile=64,
        rlimit_fsize=0,
        rpc_handlers=handlers or {},
    )


# ---------------------------------------------------------------------------
# Env flag — every test in this module needs learning ON to exercise RPC.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_learning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", "true")


# ---------------------------------------------------------------------------
# (a) No setter on QTableProxy — write attempts fail in the child.
# ---------------------------------------------------------------------------


def test_qtable_proxy_has_no_setter() -> None:
    """Agent tries ``ctx.qtable.set(...)`` — AttributeError, no setter.

    The child captures the AttributeError, classifies it as a generic
    ``error`` status, and ships back the traceback. We just check the
    raised exception name surfaces in stderr.
    """
    src = (
        "def on_tick(ctx):\n"
        "    ctx.qtable.set({'foo': 'bar'}, {'sell': 0.5})\n"
        "    return 'unreachable'\n"
    )
    engine = _engine_with_handlers()
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "error"
    # The traceback should name the missing attribute. We don't pin the
    # exact class because the proxy is a frozen dataclass: missing
    # methods raise plain ``AttributeError``.
    assert "AttributeError" in (result.stderr or "")
    assert "set" in (result.stderr or "")


def test_qtable_proxy_cannot_assign_attribute() -> None:
    """Agent tries ``ctx.qtable.foo = 1`` — FrozenInstanceError."""
    src = "def on_tick(ctx):\n    ctx.qtable.foo = 'pwned'\n    return 'unreachable'\n"
    engine = _engine_with_handlers()
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "error"
    assert "FrozenInstanceError" in (result.stderr or "")


# ---------------------------------------------------------------------------
# (b) No delete vector on EpisodicProxy.
# ---------------------------------------------------------------------------


def test_episodic_proxy_has_no_delete() -> None:
    """Episodic exposes ONLY ``.record`` — ``.delete`` / ``.pop`` etc. fail."""
    src = "def on_tick(ctx):\n    ctx.episodic.delete('some-id')\n    return 'unreachable'\n"
    engine = _engine_with_handlers()
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "error"
    assert "AttributeError" in (result.stderr or "")
    assert "delete" in (result.stderr or "")


# ---------------------------------------------------------------------------
# (c) Cross-project read defence — frozen tamper + dispatcher uses bound IDs.
# ---------------------------------------------------------------------------


def test_semantic_proxy_project_id_is_frozen() -> None:
    """Agent tries to rebind ``ctx.semantic.project_id`` — FrozenInstanceError."""
    src = (
        "def on_tick(ctx):\n"
        "    ctx.semantic.project_id = '00000000-0000-0000-0000-000000000000'\n"
        "    return 'unreachable'\n"
    )
    engine = _engine_with_handlers()
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "error"
    assert "FrozenInstanceError" in (result.stderr or "")


def test_dispatcher_uses_bound_project_id_even_if_payload_tampered() -> None:
    """Even if a payload smuggles a foreign ``project_id``, the dispatcher ignores it.

    We register a stub ``semantic.list`` handler that ECHOES the
    ``project_id`` it was invoked with. The agent then directly pokes
    the underlying RpcClient with a hand-rolled payload that names a
    foreign project. The handler must echo the BOUND project_id (passed
    in by the dispatcher from the engine), not the foreign one.
    """
    foreign_project = uuid.uuid4()

    async def echo_project(
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "echoed_user_id": str(user_id),
            "echoed_project_id": str(project_id),
        }

    handlers = {"semantic.list": echo_project}
    engine = _engine_with_handlers(handlers)

    # The agent reaches into ctx.semantic._rpc and crafts a raw payload
    # that tries to override project_id. The handler must echo back the
    # BOUND project_id (the one the engine recorded at spawn time).
    foreign = str(foreign_project)
    src = (
        "def on_tick(ctx):\n"
        f"    payload_project = '{foreign}'\n"
        "    raw = ctx.semantic._rpc.call(\n"
        "        'semantic.list',\n"
        "        project_id=payload_project,\n"
        "        user_id=payload_project,\n"
        "    )\n"
        "    return raw\n"
    )

    project = _make_project()
    bound_project_id = str(project.id)
    bound_user_id = str(project.user_id)

    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=project,
    )
    assert result.status == "success", result.stderr
    echoed = result.result
    assert isinstance(echoed, dict)
    # Critical: the dispatcher MUST have stripped the child's
    # project_id/user_id and used the bound ones.
    assert echoed["echoed_project_id"] == bound_project_id
    assert echoed["echoed_user_id"] == bound_user_id
    assert echoed["echoed_project_id"] != foreign
    assert echoed["echoed_user_id"] != foreign


# ---------------------------------------------------------------------------
# (d) EpisodicProxy user_id tampering — frozen + dispatcher defence.
# ---------------------------------------------------------------------------


def test_episodic_proxy_user_id_is_frozen() -> None:
    """Agent tries ``ctx.episodic.user_id = ...`` — FrozenInstanceError."""
    src = (
        "def on_tick(ctx):\n"
        "    ctx.episodic.user_id = '00000000-0000-0000-0000-000000000000'\n"
        "    return 'unreachable'\n"
    )
    engine = _engine_with_handlers()
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "error"
    assert "FrozenInstanceError" in (result.stderr or "")


# ---------------------------------------------------------------------------
# Integration: real subprocess + parent-side observation of episodic.record
# ---------------------------------------------------------------------------


# Module-level recorder so the stub handler is picklable-irrelevant
# (we never pickle it — the engine carries the dict reference in the
# parent process; the dispatcher thread runs handlers in-parent).
_RECORDED_EPISODES: list[dict[str, Any]] = []


def test_episodic_record_round_trips_with_bound_ids() -> None:
    """Spawn a real subprocess; the agent calls ``ctx.episodic.record(...)``.

    The parent observes the call via a stub ``episodic.record`` handler
    that appends the (user_id, project_id, action, reward) tuple to a
    list. The integration property under test: the parent saw the call
    with the BOUND ids (those of the project_row we passed in), not
    whatever the agent code might have tried to assert.
    """
    _RECORDED_EPISODES.clear()

    async def record_handler(
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        state: dict[str, Any],
        action: str,
        reward: float,
        result: Any = None,  # noqa: ARG001
        reasoning: str = "",  # noqa: ARG001
        q_before: float | None = None,  # noqa: ARG001
    ) -> dict[str, Any]:
        new_id = uuid.uuid4()
        _RECORDED_EPISODES.append(
            {
                "user_id": str(user_id),
                "project_id": str(project_id),
                "state_keys": sorted(state.keys()),
                "action": action,
                "reward": reward,
                "row_id": str(new_id),
            }
        )
        return {
            "id": str(new_id),
            "sleep_run_id": None,
            "q_value_before": None,
        }

    handlers = {"episodic.record": record_handler}
    engine = _engine_with_handlers(handlers)

    project = _make_project()
    bound_user_id = str(project.user_id)
    bound_project_id = str(project.id)

    src = (
        "def on_tick(ctx):\n"
        "    ref = ctx.episodic.record(\n"
        "        state={'rsi': 30, 'tf': 'H1'},\n"
        "        action='buy',\n"
        "        reward=0.42,\n"
        "        result={'pnl_pct': 0.42},\n"
        "        reasoning='oversold-bounce',\n"
        "    )\n"
        "    # Return primitives so the parent can unpickle cleanly.\n"
        "    return {'row_id': str(ref.id)}\n"
    )

    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=project,
    )
    assert result.status == "success", result.stderr
    assert isinstance(result.result, dict)
    returned_id = result.result["row_id"]

    # Parent saw exactly one episode with the bound IDs.
    assert len(_RECORDED_EPISODES) == 1
    rec = _RECORDED_EPISODES[0]
    assert rec["user_id"] == bound_user_id
    assert rec["project_id"] == bound_project_id
    assert rec["action"] == "buy"
    assert rec["reward"] == pytest.approx(0.42)
    assert rec["state_keys"] == ["rsi", "tf"]
    # The row_id the child saw matches what the handler returned.
    assert rec["row_id"] == returned_id


# ---------------------------------------------------------------------------
# NO-OP mode — AETHER_LEARNING_ENABLED=false binds the inert proxies.
# ---------------------------------------------------------------------------


def test_noop_mode_disables_episodic_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """``AETHER_LEARNING_ENABLED=false`` → ``ctx.episodic.record`` raises.

    NoopEpisodic raises ``RuntimeError("learning disabled")``; reads
    return None / []. We assert the runtime error and that no handler
    was ever invoked (the dispatcher isn't even spun up).
    """
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", "false")
    _RECORDED_EPISODES.clear()

    async def never_call(**_: Any) -> dict[str, Any]:
        _RECORDED_EPISODES.append({"unexpected": True})
        return {}

    handlers = {"episodic.record": never_call}
    engine = _engine_with_handlers(handlers)

    src = (
        "def on_tick(ctx):\n"
        "    return ctx.episodic.record(\n"
        "        state={'k': 1}, action='buy', reward=0.0,\n"
        "        result={}, reasoning='',\n"
        "    )\n"
    )
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "error"
    assert "learning disabled" in (result.stderr or "")
    assert _RECORDED_EPISODES == []


def test_noop_mode_qtable_get_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """``AETHER_LEARNING_ENABLED=false`` → ``ctx.qtable.get`` returns None."""
    monkeypatch.setenv("AETHER_LEARNING_ENABLED", "false")
    engine = _engine_with_handlers()
    src = "def on_tick(ctx):\n    return ctx.qtable.get({'k': 1})\n"
    result = engine.run_agent(
        agent_row=_make_agent(logica=src),
        project_row=_make_project(),
    )
    assert result.status == "success", result.stderr
    assert result.result is None

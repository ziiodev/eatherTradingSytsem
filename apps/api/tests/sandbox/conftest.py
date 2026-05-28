"""Local fixtures for the sandbox suite.

A bare :class:`Engine` instance, dialed down so the suite runs fast
(short wall clock, tight CPU limit). Each test constructs its own engine
when it needs custom rlimits, but the common case can grab ``engine`` and
go.

The :func:`fake_rows` fixture returns SimpleNamespace stand-ins for the
ORM rows the engine reads — the engine only touches a handful of
attributes (``logica``, ``entrypoint``, ``user_id``, ``id``, ``mcp_url``,
``mcp_port``, ``symbol``, ``timeframe``) so we don't need real ORM
instances to drive it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from aether_api.sandbox.engine import Engine


@pytest.fixture
def engine() -> Engine:
    """Tight rlimits so the suite runs fast on CI."""
    return Engine(
        wall_clock_seconds=5.0,
        rlimit_cpu_seconds=3,
        rlimit_as_bytes=256 * 1024 * 1024,
        rlimit_nofile=64,
        rlimit_fsize=0,
    )


def _agent(*, logica: str, entrypoint: str = "on_tick", type_: str = "worker") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        logica=logica,
        entrypoint=entrypoint,
        type=type_,
    )


def _project(*, mcp_host: str = "127.0.0.1", mcp_port: int = 65000) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        symbol="EURUSD",
        timeframe="H1",
        mcp_url=f"http://{mcp_host}:{mcp_port}",
        mcp_port=mcp_port,
    )


@pytest.fixture
def fake_agent():
    return _agent


@pytest.fixture
def fake_project():
    return _project

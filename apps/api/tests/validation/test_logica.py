"""Unit tests for :mod:`aether_api.validation.logica`.

These are pure-Python unit tests (no DB, no FastAPI) — they exercise
the validator in isolation. The router-level behaviour (422 mapping) is
covered by ``tests/test_agents_crud.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from aether_api.validation import logica as logica_module
from aether_api.validation.logica import (
    MAX_LOGICA_BYTES,
    LogicaParseTimeoutError,
    LogicaSyntaxError,
    LogicaTooLargeError,
    validate_logica_shape,
)


async def test_valid_source_returns_none() -> None:
    source = "def on_tick(ctx):\n    return None\n"
    assert await validate_logica_shape(source) is None


async def test_syntax_error_surfaces_line_and_col() -> None:
    source = "def broken(ctx)\n    return None\n"  # missing colon
    with pytest.raises(LogicaSyntaxError) as excinfo:
        await validate_logica_shape(source)
    assert excinfo.value.line == 1
    assert excinfo.value.col is not None
    assert excinfo.value.col >= 1
    assert "syntax" in str(excinfo.value).lower()


async def test_oversize_exact_boundary_passes() -> None:
    """At exactly MAX_LOGICA_BYTES the validator accepts; one more byte rejects.

    We craft a payload whose UTF-8 length is exactly the cap by padding
    a comment line — comments are valid Python so the parser is happy.
    """
    base = "# x"
    pad = "a" * (MAX_LOGICA_BYTES - len(base.encode("utf-8")))
    at_cap = base + pad
    assert len(at_cap.encode("utf-8")) == MAX_LOGICA_BYTES
    assert await validate_logica_shape(at_cap) is None

    over_cap = at_cap + "a"
    with pytest.raises(LogicaTooLargeError) as excinfo:
        await validate_logica_shape(over_cap)
    assert excinfo.value.size_bytes == MAX_LOGICA_BYTES + 1


async def test_timeout_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ``ast.parse`` exceeds the budget, validator raises timeout."""
    # Monkeypatch ast.parse to sleep longer than the budget. The validator
    # runs ast.parse in ``asyncio.to_thread`` and wraps it in
    # ``asyncio.wait_for`` — so a blocking sleep IS interrupted by the
    # timeout (asyncio.wait_for cancels the task).
    import time

    def slow_parse(*args: object, **kwargs: object) -> object:
        time.sleep(5)
        return None

    monkeypatch.setattr(logica_module.ast, "parse", slow_parse)

    with pytest.raises(LogicaParseTimeoutError):
        # Use a small, valid-looking source so we hit the patched parser.
        await validate_logica_shape("x = 1\n")


async def test_concurrent_validations_do_not_deadlock() -> None:
    """Multiple validate_logica_shape calls in parallel all succeed."""
    sources = [f"x_{i} = {i}\n" for i in range(8)]
    results = await asyncio.gather(*(validate_logica_shape(s) for s in sources))
    assert results == [None] * 8


async def test_empty_source_is_valid_python() -> None:
    """An empty module is syntactically valid — the API-layer's
    ``min_length=1`` constraint is what rejects empty submissions.
    """
    assert await validate_logica_shape("") is None

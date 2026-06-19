"""Local fixtures for the pure codegen test suite.

The codegen engine (``aether_api.services.codegen``) is framework-free: a graph
dict goes in, a source string comes out — no DB, no Settings, no I/O, no async.
These tests are therefore intentionally DB-FREE.

The top-level ``tests/conftest.py`` defines an ``autouse`` fixture
``_truncate_mutable_tables`` that depends (transitively) on a live Postgres via
``migrated_db``. Pulling that in here would force every pure codegen assertion
to stand up a database it never touches. We override the fixture by NAME with a
no-op of the same scope so the autouse machinery resolves to this closer
definition for tests under ``tests/codegen/`` only — the rest of the suite keeps
the real truncating fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _truncate_mutable_tables() -> Iterator[None]:  # noqa: PT004
    """No-op override: pure codegen tests need no database."""
    yield

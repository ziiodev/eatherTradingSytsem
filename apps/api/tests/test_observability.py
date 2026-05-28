"""Tests for the observability stack.

Coverage:

* PII scrubber masks forbidden keys + JWT-shaped strings.
* The structlog setup is idempotent and emits JSON.
* The request-ID middleware echoes incoming X-Request-ID and generates
  one when absent.
* The audit repository writes a row when ``AUDIT_LOG_ENABLED=true`` and
  no-ops when off.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import pytest
import structlog
from aether_api.core.logging import setup_logging
from aether_api.core.middleware import RequestIDMiddleware
from aether_api.core.pii import (
    FORBIDDEN_KEYS,
    MASK,
    scrub_event,
    scrub_mapping,
    scrub_pii_processor,
)


# ---------------------------------------------------------------------------
# PII scrubber
# ---------------------------------------------------------------------------
def test_scrub_pii_masks_password_key() -> None:
    event = {"event": "login attempt", "password": "hunter2"}
    out = scrub_pii_processor(None, "info", event)
    assert out["password"] == MASK
    # Non-forbidden keys are untouched.
    assert out["event"] == "login attempt"


def test_scrub_pii_masks_jwt_like_strings() -> None:
    fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc-DEF_123"
    event: dict[str, Any] = {"event": f"got token {fake_jwt} from header"}
    out = scrub_pii_processor(None, "info", event)
    assert fake_jwt not in out["event"]
    assert MASK in out["event"]


def test_scrub_pii_recurses_into_nested_mappings() -> None:
    event = {
        "event": "user create",
        "user": {"email": "a@b.com", "password_hash": "argon2..."},
    }
    out = scrub_pii_processor(None, "info", event)
    assert out["user"]["password_hash"] == MASK
    assert out["user"]["email"] == "a@b.com"


def test_scrub_pii_handles_lists() -> None:
    event = {"event": "x", "items": [{"password": "secret"}, {"name": "ok"}]}
    out = scrub_pii_processor(None, "info", event)
    assert out["items"][0]["password"] == MASK
    assert out["items"][1]["name"] == "ok"


def test_scrub_event_returns_copy_for_sentry() -> None:
    event = {"event_id": "abc", "extra": {"jwt_secret": "leakme"}}
    out = scrub_event(event)
    assert out["extra"]["jwt_secret"] == MASK
    # Top-level meta keys passed through.
    assert out["event_id"] == "abc"


def test_scrub_mapping_is_case_insensitive_for_keys() -> None:
    event = {"Password": "x", "AUTHORIZATION": "Bearer ..."}
    out = scrub_mapping(event)
    assert out["Password"] == MASK
    assert out["AUTHORIZATION"] == MASK


def test_forbidden_keys_match_design() -> None:
    """Spot-check the design-mandated forbidden keys are present."""
    must_have = {
        "password",
        "password_hash",
        "csrf_token",
        "aether_access",
        "aether_refresh",
        "mfa_secret_ref",
        "jwt_secret",
    }
    assert must_have.issubset({k.lower() for k in FORBIDDEN_KEYS})


# ---------------------------------------------------------------------------
# Structlog setup
# ---------------------------------------------------------------------------
def test_setup_logging_emits_json_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    log = structlog.get_logger("aether.test")
    log.info("hello world", request_id="r-1", user_id="u-1")
    captured = capsys.readouterr()
    # One JSON line on stdout.
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello world"
    assert payload["level"] == "info"
    assert "timestamp" in payload
    assert payload["request_id"] == "r-1"
    assert payload["user_id"] == "u-1"


def test_setup_logging_is_idempotent() -> None:
    """Re-invoking setup MUST NOT accumulate handlers."""
    setup_logging("INFO")
    root = logging.getLogger()
    n1 = len(root.handlers)
    setup_logging("INFO")
    setup_logging("INFO")
    n3 = len(root.handlers)
    assert n1 == n3 == 1


def test_setup_logging_scrubs_pii_in_output(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("INFO")
    log = structlog.get_logger("aether.test")
    log.info("auth attempt", password="should-not-appear")
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["password"] == MASK


def test_stdlib_bridge_routes_through_json(capsys: pytest.CaptureFixture[str]) -> None:
    """A plain stdlib logger.info(...) call should also produce JSON."""
    setup_logging("INFO")
    stdlib_log = logging.getLogger("aether.bridge.test")
    stdlib_log.info("bridge ok")
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "bridge ok"
    assert payload["level"] == "info"


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_id_generated_when_missing() -> None:
    """Without an incoming X-Request-ID, the middleware MUST generate a UUID."""
    seen: dict[str, Any] = {}

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        # Capture the request_id bound into contextvars.
        ctx = structlog.contextvars.get_contextvars()
        seen["bound"] = ctx.get("request_id")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = RequestIDMiddleware(downstream_app)

    sent_messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = {"type": "http", "headers": []}
    await mw(scope, receive, send)

    assert seen["bound"] is not None
    uuid.UUID(seen["bound"])  # parses

    start = next(m for m in sent_messages if m["type"] == "http.response.start")
    echoed = dict(start["headers"]).get(b"x-request-id")
    assert echoed is not None
    assert echoed.decode("ascii") == seen["bound"]


@pytest.mark.asyncio
async def test_request_id_propagated_from_incoming_header() -> None:
    """A valid UUID in X-Request-ID MUST flow through to contextvars + response."""
    incoming = "12345678-1234-5678-1234-567812345678"
    seen: dict[str, Any] = {}

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        seen["bound"] = structlog.contextvars.get_contextvars().get("request_id")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = RequestIDMiddleware(downstream_app)
    sent_messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "headers": [(b"x-request-id", incoming.encode("ascii"))],
    }
    await mw(scope, receive, send)

    assert seen["bound"] == incoming
    start = next(m for m in sent_messages if m["type"] == "http.response.start")
    echoed = dict(start["headers"]).get(b"x-request-id")
    assert echoed == incoming.encode("ascii")


@pytest.mark.asyncio
async def test_request_id_rejects_non_uuid_incoming() -> None:
    """Garbage X-Request-ID values are dropped and a fresh UUID generated."""
    seen: dict[str, Any] = {}

    async def downstream_app(scope: Any, receive: Any, send: Any) -> None:
        seen["bound"] = structlog.contextvars.get_contextvars().get("request_id")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = RequestIDMiddleware(downstream_app)

    async def send(message: dict[str, Any]) -> None:
        pass

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    scope = {
        "type": "http",
        "headers": [(b"x-request-id", b"not-a-uuid; drop table")],
    }
    await mw(scope, receive, send)
    assert seen["bound"] is not None
    uuid.UUID(seen["bound"])  # parses — middleware made a fresh one


# ---------------------------------------------------------------------------
# Audit repository — integration test (requires DB)
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_repository_records_when_enabled(migrated_db: str) -> None:
    """When AUDIT_LOG_ENABLED, ``record`` inserts a row with the scrubbed shape."""
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.models.user import User
    from aether_api.repositories.audit_repository import AuditRepository

    # Flip the flag for this test only.
    prior = os.environ.get("AUDIT_LOG_ENABLED")
    os.environ["AUDIT_LOG_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        maker = get_session_maker()
        async with maker() as session:
            user = User(
                email=f"audit-{uuid.uuid4().hex[:8]}@example.com",
                password_hash=None,
            )
            session.add(user)
            await session.flush()

            repo = AuditRepository(session)
            row = await repo.record(
                user_id=user.id,
                action="test.audit",
                target_type="user",
                target_id=user.id,
                before={"display_name": None},
                after={"display_name": "Alice"},
            )
            await session.commit()

            assert row is not None
            assert row.user_id == user.id
            assert row.action == "test.audit"
            rows = await repo.list_for_user(user.id)
            assert len(rows) == 1
            assert rows[0].action == "test.audit"
    finally:
        if prior is None:
            os.environ.pop("AUDIT_LOG_ENABLED", None)
        else:
            os.environ["AUDIT_LOG_ENABLED"] = prior
        get_settings.cache_clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_repository_noop_when_disabled(migrated_db: str) -> None:
    """With the flag OFF, ``record`` returns None and writes nothing."""
    from aether_api.core.settings import get_settings
    from aether_api.db.session import get_session_maker
    from aether_api.models.user import User
    from aether_api.repositories.audit_repository import AuditRepository

    # Ensure flag is OFF.
    prior = os.environ.pop("AUDIT_LOG_ENABLED", None)
    get_settings.cache_clear()
    try:
        maker = get_session_maker()
        async with maker() as session:
            user = User(
                email=f"audit-off-{uuid.uuid4().hex[:8]}@example.com",
                password_hash=None,
            )
            session.add(user)
            await session.flush()

            repo = AuditRepository(session)
            row = await repo.record(
                user_id=user.id,
                action="test.disabled",
                target_type="user",
            )
            await session.commit()

            assert row is None
            assert await repo.count_for_user(user.id) == 0
    finally:
        if prior is not None:
            os.environ["AUDIT_LOG_ENABLED"] = prior
        get_settings.cache_clear()

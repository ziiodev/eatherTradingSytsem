"""Golden-file + injection-rejection tests for the Dockerfile renderer.

These tests are unit-level — no DB, no settings, no Docker daemon.
A :class:`Pair` model instance (with an in-memory :class:`Account`
parent carrying the broker credentials) is built in memory and the
renderer returns a string. The "golden" assertion is that:

1. Two calls with the same pair produce **byte-identical** output
   (the spec's determinism contract).
2. Required header lines exist in the expected order.
3. A pair / account field with a forbidden character raises
   :class:`UnsafeValueError`, NEVER lands in the rendered text.

Broker / account credentials moved OFF the pair onto its Account parent
(accounts-pairs hierarchy); the renderer reads them through
``pair.account``.
"""

from __future__ import annotations

import uuid

import pytest
from aether_api.docker_control.dockerfile import render_default_dockerfile
from aether_api.docker_control.sanitize import UnsafeValueError
from aether_api.models.account import Account
from aether_api.models.pair import Pair

#: Account-level credential fields — moved off the pair onto the Account.
_ACCOUNT_FIELDS = frozenset(
    {"broker_name", "account_login", "account_server", "account_currency"}
)


def _project(**overrides) -> Pair:
    """Build an in-memory :class:`Pair` (+ Account parent) without the DB."""
    account_defaults = {
        "broker_name": "ICMarkets",
        "account_login": "demo-42",
        "account_server": "ICMarkets-Demo",
        "account_currency": "USD",
    }
    pair_defaults = {
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "user_id": uuid.uuid4(),
        "name": "Aether-EURUSD-H1",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "mcp_url": "http://mcp.local:8081",
        "mcp_port": 8081,
        "docker_image": "mt5-base:latest",
        "status": "inactive",
    }
    for key, value in overrides.items():
        if key in _ACCOUNT_FIELDS:
            account_defaults[key] = value
        else:
            pair_defaults[key] = value
    pair = Pair(**pair_defaults)
    # Assign the relationship in-memory — bypasses ``lazy="raise"`` since
    # the attribute is set directly rather than lazy-loaded.
    pair.account = Account(
        user_id=pair.user_id,
        exchange_id=uuid.uuid4(),
        name="acct-fixture",
        **account_defaults,
    )
    return pair


# ---------------------------------------------------------------------------
# Determinism — byte-identical across calls.
# ---------------------------------------------------------------------------
def test_render_is_deterministic() -> None:
    p = _project()
    a = render_default_dockerfile(p)
    b = render_default_dockerfile(p)
    assert a == b
    # And again with a fresh project instance constructed from the same args.
    c = render_default_dockerfile(_project())
    assert a == c


# ---------------------------------------------------------------------------
# Shape — header lines in expected order.
# ---------------------------------------------------------------------------
def test_render_emits_expected_header() -> None:
    body = render_default_dockerfile(_project())
    assert body.startswith("FROM mt5-base:latest\n")
    assert "LABEL aether.project_id=" in body
    assert "LABEL aether.symbol=EURUSD" in body
    assert "ENV AETHER_SYMBOL=EURUSD" in body
    assert "ENV AETHER_BROKER=ICMarkets" in body
    assert "EXPOSE 8081" in body
    assert "HEALTHCHECK" in body


def test_render_honours_custom_docker_image() -> None:
    body = render_default_dockerfile(_project(docker_image="registry/aether:1.2.3"))
    assert body.startswith("FROM registry/aether:1.2.3\n")


def test_render_omits_optional_blocks_when_null() -> None:
    body = render_default_dockerfile(
        _project(
            broker_name=None,
            account_login=None,
            account_server=None,
            account_currency=None,
            mcp_port=None,
        )
    )
    assert "LABEL aether.broker=" not in body
    assert "LABEL aether.account_login=" not in body
    assert "ENV AETHER_BROKER=" not in body
    assert "ENV AETHER_ACCOUNT_LOGIN=" not in body
    assert "EXPOSE" not in body


# ---------------------------------------------------------------------------
# Injection — every forbidden character class is rejected BEFORE Jinja.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("broker_name", "IC; rm -rf /"),
        ("broker_name", "IC\nRUN curl attacker"),
        ("account_login", "user`whoami`"),
        ("account_server", "host\nLABEL pwn=1"),
        ("account_currency", "U$D"),
        ("symbol", "EUR USD"),  # space inside symbol breaks tokenisation
        ("docker_image", "mt5 base:latest"),
        ("docker_image", "img$(date)"),
    ],
)
def test_render_rejects_injection_payload(field: str, value: str) -> None:
    p = _project(**{field: value})
    with pytest.raises(UnsafeValueError) as exc:
        render_default_dockerfile(p)
    assert exc.value.field == field


def test_render_rejects_unsafe_base_image_override() -> None:
    p = _project()
    with pytest.raises(UnsafeValueError) as exc:
        render_default_dockerfile(p, base_image="image with space")
    assert exc.value.field == "docker_image"


def test_render_label_safe_strips_punctuation_in_project_name() -> None:
    # Project names allow spaces / commas / parens upstream; the label
    # value is filtered down to the strict allowlist via _label_safe.
    body = render_default_dockerfile(_project(name="My Project, v1 (alpha)"))
    assert "LABEL aether.project_name=" in body
    # No raw space / comma / parens make it into the label value.
    for forbidden in (" ", ",", "(", ")"):
        # The label line is one line per LABEL directive.
        for line in body.splitlines():
            if line.startswith("LABEL aether.project_name="):
                # Allow leading slash etc. but no space / comma / parens.
                value = line.split("=", 1)[1]
                assert forbidden not in value

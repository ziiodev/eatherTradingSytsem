"""Tests for :mod:`aether_api.services.chat.anthropic_client`.

Anthropic SDK is fully mocked — no network. Tests cover:

* Pricing table values for the v1 whitelist + ``calc_usd`` math.
* Model whitelist enforcement (default + rejection of unknown).
* Two-block system prompt validation: block 1 MUST have
  ``cache_control``; block 2 MUST NOT.
* ``stream_assistant_turn`` passes ``max_tokens=4096``, system prompt,
  tools, and resolved model verbatim to ``client.messages.stream``.
* Cache-control snapshot test: the kwargs the SDK sees include the
  static block with ``cache_control={"type":"ephemeral"}``.
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Pricing + USD math
# ---------------------------------------------------------------------------


def test_pricing_table_shape() -> None:
    from aether_api.services.chat.anthropic_client import (
        MODEL_PRICING_USD_PER_M,
        MODEL_WHITELIST,
    )

    assert frozenset(MODEL_PRICING_USD_PER_M.keys()) == MODEL_WHITELIST
    assert MODEL_PRICING_USD_PER_M == {
        "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    }


def test_calc_usd_with_dict_usage() -> None:
    from aether_api.services.chat.anthropic_client import calc_usd

    # 1M input @ 3.0 + 1M output @ 15.0 = 3 + 15 = 18.0 USD
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert calc_usd(usage, "claude-sonnet-4-5") == 18.0

    # Same usage on haiku: 1 + 5 = 6.0 USD
    assert calc_usd(usage, "claude-haiku-4-5") == 6.0


def test_calc_usd_with_object_usage() -> None:
    from aether_api.services.chat.anthropic_client import calc_usd

    class _U:
        input_tokens = 500
        output_tokens = 1000

    # 500 * 3 + 1000 * 15 = 1500 + 15000 = 16500 / 1e6 = 0.0165
    assert calc_usd(_U(), "claude-sonnet-4-5") == pytest.approx(0.0165)


def test_calc_usd_unknown_model_returns_zero() -> None:
    from aether_api.services.chat.anthropic_client import calc_usd

    assert calc_usd({"input_tokens": 1000, "output_tokens": 1000}, "ghost-1") == 0.0


# ---------------------------------------------------------------------------
# Model resolution + system prompt validation
# ---------------------------------------------------------------------------


def test_default_model() -> None:
    from aether_api.services.chat.anthropic_client import DEFAULT_MODEL

    assert DEFAULT_MODEL == "claude-sonnet-4-5"


def test_stream_assistant_turn_rejects_unknown_model() -> None:
    from aether_api.services.chat.anthropic_client import stream_assistant_turn

    fake_client = _FakeClient()
    system = [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic"},
    ]
    with pytest.raises(ValueError, match="whitelist"):
        stream_assistant_turn(
            fake_client,
            system=system,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            model_override="claude-opus-99",
        )


def test_stream_assistant_turn_rejects_missing_cache_control() -> None:
    """Block 1 must carry cache_control — refuse silent cache-miss bug."""
    from aether_api.services.chat.anthropic_client import stream_assistant_turn

    fake_client = _FakeClient()
    bad_system = [
        {"type": "text", "text": "static, no cache_control"},
        {"type": "text", "text": "dynamic"},
    ]
    with pytest.raises(ValueError, match="cache_control"):
        stream_assistant_turn(
            fake_client,
            system=bad_system,
            messages=[],
            tools=None,
        )


def test_stream_assistant_turn_rejects_cache_control_on_block_two() -> None:
    """Block 2 must NOT carry cache_control — caching dynamic snapshot is a bug."""
    from aether_api.services.chat.anthropic_client import stream_assistant_turn

    fake_client = _FakeClient()
    bad_system = [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic", "cache_control": {"type": "ephemeral"}},
    ]
    with pytest.raises(ValueError, match="MUST NOT carry"):
        stream_assistant_turn(
            fake_client,
            system=bad_system,
            messages=[],
            tools=None,
        )


def test_stream_assistant_turn_rejects_wrong_block_count() -> None:
    from aether_api.services.chat.anthropic_client import stream_assistant_turn

    fake_client = _FakeClient()
    with pytest.raises(ValueError, match="two blocks"):
        stream_assistant_turn(
            fake_client,
            system=[
                {"type": "text", "text": "only one", "cache_control": {"type": "ephemeral"}},
            ],
            messages=[],
            tools=None,
        )


# ---------------------------------------------------------------------------
# Cache control + max_tokens snapshot on the SDK call
# ---------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs

        class _CM:
            async def __aenter__(self_inner):
                return iter([])

            async def __aexit__(self_inner, *args):
                return False

        return _CM()


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


def test_stream_assistant_turn_passes_expected_kwargs() -> None:
    from aether_api.services.chat.anthropic_client import (
        MAX_OUTPUT_TOKENS,
        stream_assistant_turn,
    )

    client = _FakeClient()
    system = [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic snapshot"},
    ]
    messages = [{"role": "user", "content": "hola"}]
    tools = [{"name": "x", "description": "y", "input_schema": {"type": "object"}}]
    stream_assistant_turn(
        client,
        system=system,
        messages=messages,
        tools=tools,
        model_override="claude-haiku-4-5",
    )
    assert client.messages.last_kwargs is not None
    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == MAX_OUTPUT_TOKENS == 4096
    assert kwargs["system"] == system  # passed verbatim, including cache_control
    assert kwargs["messages"] == messages
    assert kwargs["tools"] == tools

    # SNAPSHOT: block 1 has cache_control, block 2 does not.
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["system"][1]


def test_stream_assistant_turn_default_model_when_none() -> None:
    from aether_api.services.chat.anthropic_client import (
        DEFAULT_MODEL,
        stream_assistant_turn,
    )

    client = _FakeClient()
    system = [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic"},
    ]
    stream_assistant_turn(
        client,
        system=system,
        messages=[],
        tools=None,
        model_override=None,
    )
    assert client.messages.last_kwargs["model"] == DEFAULT_MODEL


def test_stream_assistant_turn_omits_tools_when_empty() -> None:
    from aether_api.services.chat.anthropic_client import stream_assistant_turn

    client = _FakeClient()
    system = [
        {"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "d"},
    ]
    stream_assistant_turn(client, system=system, messages=[], tools=None)
    assert "tools" not in client.messages.last_kwargs


def test_stream_assistant_turn_raises_when_client_none() -> None:
    from aether_api.services.chat.anthropic_client import (
        AnthropicClientNotConfiguredError,
        stream_assistant_turn,
    )

    with pytest.raises(AnthropicClientNotConfiguredError):
        stream_assistant_turn(
            None,
            system=[
                {"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "d"},
            ],
            messages=[],
            tools=None,
        )


def test_catalogue_to_anthropic_tools_shape() -> None:
    from aether_api.services.chat.anthropic_client import (
        catalogue_to_anthropic_tools,
    )

    tools = catalogue_to_anthropic_tools()
    names = {t["name"] for t in tools}
    assert names == {
        "get_project_status",
        "get_recent_trades",
        "get_sleep_reports",
        "get_qtable_summary",
        "get_semantic_rules",
    }
    for t in tools:
        assert set(t.keys()) == {"name", "description", "input_schema"}
        assert isinstance(t["input_schema"], dict)
        # No tenancy keys in the catalogue translation either.
        props = t["input_schema"].get("properties", {})
        assert not set(props.keys()) & {"user_id", "project_id", "conversation_id"}

"""Thin wrapper around :class:`anthropic.Anthropic`'s streaming Messages API.

The wrapper enforces three project-level invariants that bare SDK calls
do not:

1. **Model whitelist** — only ``claude-sonnet-4-5`` and
   ``claude-haiku-4-5`` are accepted. Any other model name is rejected
   at the call boundary. The default is ``claude-sonnet-4-5``.
2. **Prompt caching** — the system prompt is a two-block list where the
   first block carries ``cache_control={"type":"ephemeral"}``. The
   wrapper does not synthesise the system prompt itself; callers pass
   the prebuilt list from :mod:`aether_api.services.chat.context`. The
   wrapper DOES guard against the second block accidentally carrying
   ``cache_control`` (a cache miss bug we don't want to debug
   silently).
3. **Pricing table** — the per-million-token USD costs are kept in this
   file as a constant so the chat-service token-counting code does not
   have to reach into the SDK for them. The constant carries the date
   it was last verified.

The wrapper returns the raw Anthropic ``MessageStream`` async context
manager (or whatever the test fake substitutes for it). Callers iterate
events themselves — keeping the wrapper agnostic to event types makes
it cheap to mock.
"""

from __future__ import annotations

from typing import Any

#: Pricing checked against Anthropic's public list on 2026-05-29. Update
#: this constant (and the comment) whenever the table moves; the chat
#: dispatcher relies on it for the running USD estimate.
MODEL_PRICING_USD_PER_M: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}

#: Whitelist of model names the dispatcher may invoke.
MODEL_WHITELIST: frozenset[str] = frozenset(MODEL_PRICING_USD_PER_M.keys())

#: Default model when the conversation does not pin one in
#: ``meta_data.model_override``.
DEFAULT_MODEL: str = "claude-sonnet-4-5"

#: Hard cap on output tokens per turn. Matches the design's "no
#: runaway generations" budget.
MAX_OUTPUT_TOKENS: int = 4096


class AnthropicClientNotConfiguredError(RuntimeError):
    """Raised when the wrapper is asked to stream but the SDK isn't wired.

    Surfaces as a structured 503 at the router layer.
    """


def _validate_system_prompt(system: list[dict[str, Any]]) -> None:
    """Guard rails on the two-block system prompt.

    * Exactly two blocks expected — block 1 (static) and block 2
      (dynamic snapshot).
    * Block 1 MUST carry ``cache_control={"type":"ephemeral"}``.
    * Block 2 MUST NOT carry ``cache_control`` — caching the per-turn
      snapshot is a correctness bug, not a performance choice.
    """
    if not isinstance(system, list) or len(system) != 2:
        raise ValueError(
            "system prompt must be a list of exactly two blocks "
            f"(got {len(system) if isinstance(system, list) else type(system).__name__})"
        )
    static_block, dynamic_block = system[0], system[1]
    if not isinstance(static_block, dict) or not isinstance(dynamic_block, dict):
        raise ValueError("system prompt blocks must be dicts")
    if static_block.get("cache_control") != {"type": "ephemeral"}:
        raise ValueError(
            "system prompt block 1 must declare cache_control "
            "{'type': 'ephemeral'}; otherwise prompt-caching is disabled"
        )
    if "cache_control" in dynamic_block:
        raise ValueError(
            "system prompt block 2 (dynamic snapshot) MUST NOT carry "
            "cache_control — it changes every turn and caching it "
            "would re-use stale state"
        )


def _resolve_model(model_override: str | None) -> str:
    """Pick the model name to send, applying the whitelist."""
    if model_override is None:
        return DEFAULT_MODEL
    if model_override not in MODEL_WHITELIST:
        raise ValueError(
            f"model {model_override!r} is not in the whitelist "
            f"{sorted(MODEL_WHITELIST)!r}"
        )
    return model_override


def calc_usd(usage: Any, model: str) -> float:
    """Compute the running USD cost for a single ``usage`` block.

    ``usage`` is the Anthropic ``Usage`` shape — either an SDK object
    with ``input_tokens`` / ``output_tokens`` attributes or a dict with
    the same keys (the tests pass dicts; production passes SDK objects).
    Unknown models default to a cost of 0 with a defensive fallback —
    callers should have rejected the model in :func:`_resolve_model`
    before reaching this point, so any 0 here is a real outlier worth
    surfacing in logs.
    """
    pricing = MODEL_PRICING_USD_PER_M.get(model)
    if pricing is None:
        return 0.0

    if isinstance(usage, dict):
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
    else:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    cost = (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / 1_000_000.0
    return float(cost)


def stream_assistant_turn(
    client: Any,
    *,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model_override: str | None = None,
    max_tokens: int = MAX_OUTPUT_TOKENS,
) -> Any:
    """Open an Anthropic streaming Messages call and return its context.

    The function does NOT iterate the stream — it returns the
    ``messages.stream(...)`` async context manager (or its test
    substitute) so the caller can ``async with ...`` it and iterate
    events at its own pace. This is the same shape the Anthropic SDK
    expects for streaming.

    ``client`` is any object with the ``messages.stream(...)`` shape —
    in production a real ``anthropic.Anthropic`` instance, in tests a
    fake whose ``stream`` returns a recorded sequence of events.
    """
    if client is None:
        raise AnthropicClientNotConfiguredError(
            "anthropic client is not configured; cannot stream"
        )

    _validate_system_prompt(system)
    model = _resolve_model(model_override)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": int(max_tokens),
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools

    return client.messages.stream(**kwargs)


def catalogue_to_anthropic_tools(
    catalogue: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Translate the project's :data:`TOOL_CATALOGUE` to Anthropic's tool array.

    Lazy import of the catalogue to avoid a circular dependency at
    module import (tools.py imports context which doesn't depend on
    anthropic_client, but the catalogue object is owned by tools.py).
    """
    if catalogue is None:
        from aether_api.services.chat.tools import TOOL_CATALOGUE

        catalogue = TOOL_CATALOGUE

    out: list[dict[str, Any]] = []
    for spec in catalogue.values():
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.schema,
            }
        )
    return out


__all__ = [
    "DEFAULT_MODEL",
    "MAX_OUTPUT_TOKENS",
    "MODEL_PRICING_USD_PER_M",
    "MODEL_WHITELIST",
    "AnthropicClientNotConfiguredError",
    "calc_usd",
    "catalogue_to_anthropic_tools",
    "stream_assistant_turn",
]

"""Chat service package — project-scoped LLM dispatcher (v1 read-only).

Public exports collapse the five sub-modules into a single import surface
so routers and tests do not need to know the internal file layout. The
sub-modules are split for testability — each can be exercised
independently with the Anthropic SDK mocked.

Sub-modules:

* :mod:`aether_api.services.chat.context` — frozen dispatch context plus
  system-prompt assembly (the two-block layout that powers prompt
  caching).
* :mod:`aether_api.services.chat.tools` — five read-only tool callables
  plus the catalogue and the tenant-safe dispatcher.
* :mod:`aether_api.services.chat.anthropic_client` — thin wrapper around
  ``anthropic.Anthropic(...).messages.stream(...)`` with the project's
  model whitelist, pricing table and cache_control mandate.
* :mod:`aether_api.services.chat.stream` — SSE generator wiring all of
  the above into a tool-round-tripped turn.
* :mod:`aether_api.services.chat.sweeper` — background loop that marks
  in-flight assistant rows orphaned by a writer disconnect.
"""

from __future__ import annotations

from aether_api.services.chat.anthropic_client import (
    MODEL_PRICING_USD_PER_M,
    MODEL_WHITELIST,
    AnthropicClientNotConfiguredError,
    calc_usd,
    stream_assistant_turn,
)
from aether_api.services.chat.context import (
    ChatDispatchContext,
    build_project_snapshot,
    build_system_prompt,
)
from aether_api.services.chat.stream import (
    TOOL_ROUNDTRIP_LIMIT,
    generate_sse_events,
)
from aether_api.services.chat.sweeper import chat_aborted_sweeper
from aether_api.services.chat.tools import (
    TOOL_CATALOGUE,
    ToolSpec,
    dispatch_tool,
)

__all__ = [
    "MODEL_PRICING_USD_PER_M",
    "MODEL_WHITELIST",
    "TOOL_CATALOGUE",
    "TOOL_ROUNDTRIP_LIMIT",
    "AnthropicClientNotConfiguredError",
    "ChatDispatchContext",
    "ToolSpec",
    "build_project_snapshot",
    "build_system_prompt",
    "calc_usd",
    "chat_aborted_sweeper",
    "dispatch_tool",
    "generate_sse_events",
    "stream_assistant_turn",
]

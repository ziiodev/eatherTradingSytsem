"""Server-Sent Events generator for one assistant turn.

The generator orchestrates:

1. Persistence of the inbound user message.
2. System-prompt + tool-array assembly.
3. The Anthropic streaming Messages call.
4. Tool-use round-trips (capped by ``TOOL_ROUNDTRIP_LIMIT``).
5. Final persistence of the assistant message (full content + tool
   calls + tool results + token accounting + stop_reason).
6. Atomic update of the conversation's running token / USD counters.

The caller is responsible for the pg_advisory_xact_lock that guards
concurrent writers on the same ``conversation_id`` — this function
assumes the lock is already held.

Wire format (SSE):

* ``event: token`` — ``{"delta": "<text>"}`` per streaming token.
* ``event: tool_use`` — ``{"tool_use_id", "tool_name", "input"}``.
* ``event: tool_result`` — ``{"tool_use_id", "output"}``.
* ``event: turn_done`` — ``{"stop_reason", "tokens_in", "tokens_out",
  "model", "usd_estimated", "soft_warning"?}``.
* ``event: error`` — ``{"code", "message"}``. Stream terminates after.

The generator never raises through the SSE boundary — exceptions are
caught, persisted as ``stop_reason='aborted'`` with whatever partial
content was accumulated, and emitted as an ``error`` event.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from aether_api.repositories.chat_conversation_repository import (
    ChatConversationRepository,
)
from aether_api.repositories.chat_message_repository import ChatMessageRepository
from aether_api.services.chat.anthropic_client import (
    DEFAULT_MODEL,
    MAX_OUTPUT_TOKENS,
    calc_usd,
    catalogue_to_anthropic_tools,
    stream_assistant_turn,
)
from aether_api.services.chat.context import (
    ChatDispatchContext,
    build_project_snapshot,
    build_system_prompt,
)
from aether_api.services.chat.tools import dispatch_tool

logger = logging.getLogger(__name__)

#: Hard cap on assistant→tool→assistant round-trips per turn. A 6th
#: tool_use surfaces ``TOOL_ROUNDTRIP_LIMIT`` and persists the partial
#: assistant message.
TOOL_ROUNDTRIP_LIMIT: int = 5

#: Conversation-level token budget. Once the running total crosses this
#: threshold the turn emits a ``soft_warning`` in ``turn_done`` so the
#: UI can hint at archival. Not enforced — it's purely advisory.
SOFT_WARNING_TOKEN_THRESHOLD: int = 200_000


def _sse(event: str, payload: dict[str, Any]) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _extract_event_kind(event: Any) -> str:
    """Best-effort accessor for the stream-event tag.

    Both the SDK objects and the test fakes expose ``.type`` (string).
    Dict-shaped events from the fakes fall back to ``event["type"]``.
    """
    if isinstance(event, dict):
        return str(event.get("type", ""))
    return str(getattr(event, "type", ""))


def _event_attr(event: Any, key: str, default: Any = None) -> Any:
    """Read an attribute from an event whether it's a dict or an SDK obj."""
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


async def _collect_history(
    *,
    session_factory: Any,
    user_id: Any,
    conversation_id: Any,
    history_limit: int = 100,
) -> list[dict[str, Any]]:
    """Materialise the conversation history into the Anthropic-shaped
    ``messages`` list, chronologically.

    Tool-shaped messages are flattened into the assistant turn that
    spawned them (Anthropic expects ``tool_result`` blocks inside a
    ``role='user'`` message — but here we keep the simple flattening
    that matches the persistence layout: assistant + user turns only).
    """
    async with session_factory() as session:
        repo = ChatMessageRepository(session)
        rows, _total = await repo.list_for_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=history_limit,
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.role == "user":
            out.append({"role": "user", "content": row.content})
        elif row.role == "assistant":
            out.append({"role": "assistant", "content": row.content})
        # role='tool' and role='system' are intentionally NOT replayed
        # to the model — system prompt is rebuilt fresh and tool turns
        # are an internal detail of the previous round.
    return out


async def generate_sse_events(
    ctx: ChatDispatchContext,
    *,
    user_message: str,
    model_override: str | None = None,
) -> AsyncIterator[str]:
    """Drive a full assistant turn and yield SSE frames as bytes-of-text.

    Caller MUST hold ``pg_advisory_xact_lock`` for the conversation.
    """
    # ------------------------------------------------------------------
    # 1. Persist the user message (separate transaction so a downstream
    #    Anthropic failure does NOT lose the user's input).
    # ------------------------------------------------------------------
    async with ctx.db_session_factory() as session:
        msg_repo = ChatMessageRepository(session)
        await msg_repo.insert_user(
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            content=user_message,
        )
        await session.commit()

    # ------------------------------------------------------------------
    # 2. Build the snapshot + system prompt + tools.
    # ------------------------------------------------------------------
    async with ctx.db_session_factory() as session:
        snapshot = await build_project_snapshot(
            session,
            user_id=ctx.user_id,
            project_id=ctx.project_id,
        )
    system_prompt = build_system_prompt(snapshot)
    tools = catalogue_to_anthropic_tools()
    history = await _collect_history(
        session_factory=ctx.db_session_factory,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
    )
    if not history:
        # Defensive — the user we just persisted should always show up,
        # but if list_for_conversation hasn't flushed yet seed the
        # message ourselves.
        history = [{"role": "user", "content": user_message}]

    # ------------------------------------------------------------------
    # 3. Drive the streaming Anthropic call + tool round-trips.
    # ------------------------------------------------------------------
    resolved_model = model_override or DEFAULT_MODEL
    accumulated_text: list[str] = []
    accumulated_tool_calls: list[dict[str, Any]] = []
    accumulated_tool_results: list[dict[str, Any]] = []
    final_stop_reason: str | None = None
    final_tokens_in = 0
    final_tokens_out = 0
    cumulative_usd = 0.0
    roundtrips = 0
    aborted = False

    try:
        messages_for_call: list[dict[str, Any]] = list(history)
        while True:
            stream_cm = stream_assistant_turn(
                ctx.llm_client,
                system=system_prompt,
                messages=messages_for_call,
                tools=tools,
                model_override=model_override,
                max_tokens=MAX_OUTPUT_TOKENS,
            )

            pending_tool_uses: list[dict[str, Any]] = []
            turn_text: list[str] = []

            async with stream_cm as stream:
                # Each iteration is a raw Anthropic stream event. We
                # only react to the ones we care about; the rest are
                # silently passed over.
                async for event in stream:
                    kind = _extract_event_kind(event)
                    if kind == "text" or kind == "text_delta":
                        delta = _event_attr(event, "text", "") or _event_attr(
                            event, "delta", ""
                        )
                        if delta:
                            turn_text.append(delta)
                            yield _sse("token", {"delta": delta})
                    elif kind == "content_block_start":
                        block = _event_attr(event, "content_block", {})
                        block_type = (
                            block.get("type")
                            if isinstance(block, dict)
                            else getattr(block, "type", None)
                        )
                        if block_type == "tool_use":
                            block_id = (
                                block.get("id")
                                if isinstance(block, dict)
                                else getattr(block, "id", None)
                            )
                            block_name = (
                                block.get("name")
                                if isinstance(block, dict)
                                else getattr(block, "name", None)
                            )
                            block_input = (
                                block.get("input", {})
                                if isinstance(block, dict)
                                else getattr(block, "input", {})
                            )
                            pending_tool_uses.append(
                                {
                                    "id": block_id,
                                    "name": block_name,
                                    "input": block_input or {},
                                }
                            )
                    elif kind == "message_delta":
                        usage = _event_attr(event, "usage", None)
                        stop_reason = _event_attr(event, "stop_reason", None)
                        if usage is not None:
                            if isinstance(usage, dict):
                                final_tokens_in += int(
                                    usage.get("input_tokens", 0) or 0
                                )
                                final_tokens_out += int(
                                    usage.get("output_tokens", 0) or 0
                                )
                            else:
                                final_tokens_in += int(
                                    getattr(usage, "input_tokens", 0) or 0
                                )
                                final_tokens_out += int(
                                    getattr(usage, "output_tokens", 0) or 0
                                )
                            cumulative_usd += calc_usd(usage, resolved_model)
                        if stop_reason is not None:
                            final_stop_reason = stop_reason
                    elif kind == "message_stop":
                        # Some SDKs emit usage on message_stop; capture
                        # defensively. Falls through to the loop exit.
                        usage = _event_attr(event, "usage", None)
                        if usage is not None:
                            cumulative_usd += calc_usd(usage, resolved_model)

            accumulated_text.append("".join(turn_text))

            if not pending_tool_uses:
                # No tool requested — the assistant turn is done.
                break

            # Tool round-trip — but only up to TOOL_ROUNDTRIP_LIMIT.
            for use in pending_tool_uses:
                if roundtrips >= TOOL_ROUNDTRIP_LIMIT:
                    aborted = True
                    final_stop_reason = "tool_roundtrip_limit"
                    yield _sse(
                        "error",
                        {
                            "code": "TOOL_ROUNDTRIP_LIMIT",
                            "message": (
                                f"Assistant exceeded {TOOL_ROUNDTRIP_LIMIT} "
                                "tool round-trips; aborting."
                            ),
                        },
                    )
                    break

                roundtrips += 1
                tool_use_id = str(use.get("id") or f"tu_{roundtrips}")
                tool_name = str(use.get("name") or "")
                tool_input = use.get("input") or {}
                accumulated_tool_calls.append(
                    {
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input,
                    }
                )
                yield _sse(
                    "tool_use",
                    {
                        "tool_use_id": tool_use_id,
                        "tool_name": tool_name,
                        "input": tool_input,
                    },
                )
                result = await dispatch_tool(
                    ctx,
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    input=tool_input,
                )
                accumulated_tool_results.append(result)
                yield _sse(
                    "tool_result",
                    {
                        "tool_use_id": tool_use_id,
                        "output": result.get("content"),
                        "is_error": bool(result.get("is_error")),
                    },
                )
                # Feed the tool result back into the next call.
                messages_for_call = list(messages_for_call) + [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": tool_use_id,
                                "name": tool_name,
                                "input": tool_input,
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps(
                                    result.get("content"),
                                    ensure_ascii=False,
                                    default=str,
                                ),
                                "is_error": bool(result.get("is_error")),
                            }
                        ],
                    },
                ]

            if aborted:
                break

        # End of streaming loop. ``aborted`` may still be False (happy
        # path); ``final_stop_reason`` may still be None if the upstream
        # never emitted message_delta — treat that as "end_turn".
        if final_stop_reason is None:
            final_stop_reason = "end_turn"

    except Exception as exc:  # noqa: BLE001 — convert to SSE error frame
        logger.warning(
            "chat stream aborted by exception: %s", type(exc).__name__,
            exc_info=True,
        )
        aborted = True
        final_stop_reason = "aborted"
        yield _sse(
            "error",
            {
                "code": "STREAM_INTERRUPTED",
                "message": f"Stream interrupted: {type(exc).__name__}.",
            },
        )

    # ------------------------------------------------------------------
    # 4. Persist the assistant message (always — even on abort).
    # ------------------------------------------------------------------
    final_content = "".join(accumulated_text)
    try:
        async with ctx.db_session_factory() as session:
            msg_repo = ChatMessageRepository(session)
            await msg_repo.insert_assistant(
                user_id=ctx.user_id,
                conversation_id=ctx.conversation_id,
                content=final_content,
                tool_calls=accumulated_tool_calls or None,
                tokens_in=final_tokens_in or None,
                tokens_out=final_tokens_out or None,
                model=resolved_model,
                stop_reason=final_stop_reason,
            )
            # Carry the tool results in a separate ``tool`` turn so the
            # next time we re-load the conversation history we still
            # have a record of what the dispatcher returned.
            if accumulated_tool_results:
                await msg_repo.insert_tool(
                    user_id=ctx.user_id,
                    conversation_id=ctx.conversation_id,
                    tool_results=accumulated_tool_results,
                )
            await session.commit()
    except Exception:  # noqa: BLE001 — never break the stream on persist
        logger.exception("failed to persist assistant message")

    # ------------------------------------------------------------------
    # 5. Atomic running-total update + final turn_done frame.
    # ------------------------------------------------------------------
    if not aborted:
        try:
            async with ctx.db_session_factory() as session:
                conv_repo = ChatConversationRepository(session)
                await conv_repo.increment_tokens(
                    user_id=ctx.user_id,
                    conversation_id=ctx.conversation_id,
                    tokens_in_delta=final_tokens_in + final_tokens_out,
                    usd_delta=cumulative_usd,
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — counters are advisory
            logger.exception("failed to update conversation rollups")

        soft_warning: bool = final_tokens_in + final_tokens_out > 0 and (
            final_tokens_in + final_tokens_out > SOFT_WARNING_TOKEN_THRESHOLD
        )
        # Also flag if the running conversation total already crossed
        # the threshold — best-effort (the recent UPDATE may not have
        # flushed yet; that's fine).
        yield _sse(
            "turn_done",
            {
                "stop_reason": final_stop_reason,
                "tokens_in": final_tokens_in,
                "tokens_out": final_tokens_out,
                "model": resolved_model,
                "usd_estimated": round(cumulative_usd, 6),
                "soft_warning": soft_warning,
            },
        )


__all__ = ["TOOL_ROUNDTRIP_LIMIT", "generate_sse_events"]

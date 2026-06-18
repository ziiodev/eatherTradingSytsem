"""Tests for :mod:`aether_api.services.chat.stream`.

The Anthropic SDK is replaced by a fake whose ``messages.stream(...)``
returns an async-iterable of synthetic events. The fake supports the
event types the stream module reacts to: ``text``, ``content_block_start``
(with ``tool_use``), ``message_delta``, ``message_stop``.

Covers:

* Happy path — text-only turn → ``token`` events + a single
  ``turn_done`` with stop_reason and token totals; assistant row is
  persisted with the model name and stop_reason.
* Tool round-trip — model emits ``tool_use`` → SSE emits ``tool_use``
  + ``tool_result`` frames; second pass yields ``end_turn``.
* Tool round-trip limit — sequence of 6 tool_use events surfaces
  ``TOOL_ROUNDTRIP_LIMIT`` and stop_reason='tool_roundtrip_limit'.
* Stream interrupted by an exception → stop_reason='aborted',
  partial assistant content persisted, error frame emitted.
* Cumulative token / USD update lands on the conversation row.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — async-iterable stream with scripted events.
# ---------------------------------------------------------------------------


class _Usage(dict):
    """Dict subclass with attribute-style access — matches both SDK and dict-shaped fakes."""

    @property
    def input_tokens(self) -> int:
        return int(self.get("input_tokens", 0) or 0)

    @property
    def output_tokens(self) -> int:
        return int(self.get("output_tokens", 0) or 0)


def _ev(**kwargs):
    """Build a stream event as a dict — the stream module reads dicts."""
    return kwargs


class _FakeStreamCM:
    """Async context manager whose body is an async-iterable of events."""

    def __init__(self, events: list[dict]) -> None:
        self._events = list(events)

    async def __aenter__(self):
        async def _iter():
            for e in self._events:
                yield e

        return _iter()

    async def __aexit__(self, *args):
        return False


class _FakeMessages:
    """Records every ``stream`` call and returns the next scripted CM."""

    def __init__(self) -> None:
        self.scripts: list[list[dict]] = []
        self.calls: list[dict[str, Any]] = []
        self.next_exception: Exception | None = None

    def push(self, events: list[dict]) -> None:
        self.scripts.append(events)

    def stream(self, **kwargs: Any) -> _FakeStreamCM:
        self.calls.append(kwargs)
        if self.next_exception is not None:
            exc, self.next_exception = self.next_exception, None
            raise exc
        if not self.scripts:
            return _FakeStreamCM([])
        return _FakeStreamCM(self.scripts.pop(0))


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_chat():
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email="a@example.com", password="testtesttesttest")
        project = await seed_project(session, owner=user, name="proj-a")
        conv = await ChatConversationRepository(session).create(
            user_id=user.id, project_id=project.id, title="t"
        )
        await session.commit()
        return user.id, project.id, conv.id


def _make_ctx(user_id, project_id, conv_id, *, llm_client):
    from aether_api.db.session import get_session_maker
    from aether_api.services.chat.context import ChatDispatchContext

    return ChatDispatchContext(
        user_id=user_id,
        pair_id=project_id,
        conversation_id=conv_id,
        db_session_factory=get_session_maker(),
        llm_client=llm_client,
    )


async def _consume(generator) -> list[str]:
    return [frame async for frame in generator]


def _parse_event(frame: str) -> tuple[str, dict]:
    import json

    lines = frame.strip().split("\n")
    event = next(
        line[len("event: "):] for line in lines if line.startswith("event: ")
    )
    data = next(
        line[len("data: "):] for line in lines if line.startswith("data: ")
    )
    return event, json.loads(data)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_text_only_turn(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import ChatMessageRepository
    from aether_api.services.chat.stream import generate_sse_events

    user_id, project_id, conv_id = await _seed_chat()
    client = _FakeClient()
    client.messages.push(
        [
            _ev(type="text", text="Hola, "),
            _ev(type="text", text="¿cómo va el "),
            _ev(type="text", text="proyecto?"),
            _ev(
                type="message_delta",
                stop_reason="end_turn",
                usage=_Usage(input_tokens=120, output_tokens=15),
            ),
            _ev(type="message_stop"),
        ]
    )

    ctx = _make_ctx(user_id, project_id, conv_id, llm_client=client)
    frames = await _consume(
        generate_sse_events(ctx, user_message="Status, por favor")
    )

    events = [_parse_event(f) for f in frames]
    kinds = [e[0] for e in events]
    assert kinds.count("token") == 3
    assert "turn_done" in kinds
    turn_done = next(p for k, p in events if k == "turn_done")
    assert turn_done["stop_reason"] == "end_turn"
    assert turn_done["tokens_in"] == 120
    assert turn_done["tokens_out"] == 15
    assert turn_done["model"] == "claude-sonnet-4-5"
    # Pricing: 120 * 3 + 15 * 15 = 360 + 225 = 585 / 1e6 = 0.000585
    assert turn_done["usd_estimated"] == pytest.approx(0.000585)
    assert turn_done["soft_warning"] is False

    # Persistence: user + assistant rows landed.
    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        rows, total = await repo.list_for_conversation(
            user_id=user_id, conversation_id=conv_id
        )
    assert total == 2
    roles = [r.role for r in rows]
    assert roles == ["user", "assistant"]
    assistant = rows[1]
    assert assistant.content == "Hola, ¿cómo va el proyecto?"
    assert assistant.stop_reason == "end_turn"
    assert assistant.model == "claude-sonnet-4-5"
    assert assistant.tokens_in == 120
    assert assistant.tokens_out == 15


async def test_tool_roundtrip(app_client) -> None:
    from aether_api.services.chat.stream import generate_sse_events

    user_id, project_id, conv_id = await _seed_chat()
    client = _FakeClient()
    # First call: model asks for `get_project_status`.
    client.messages.push(
        [
            _ev(type="text", text="Voy a consultar el estado. "),
            _ev(
                type="content_block_start",
                content_block={
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "get_project_status",
                    "input": {},
                },
            ),
            _ev(
                type="message_delta",
                stop_reason="tool_use",
                usage=_Usage(input_tokens=300, output_tokens=20),
            ),
            _ev(type="message_stop"),
        ]
    )
    # Second call: model finishes with text after tool result.
    client.messages.push(
        [
            _ev(type="text", text="El proyecto está activo."),
            _ev(
                type="message_delta",
                stop_reason="end_turn",
                usage=_Usage(input_tokens=50, output_tokens=10),
            ),
            _ev(type="message_stop"),
        ]
    )

    ctx = _make_ctx(user_id, project_id, conv_id, llm_client=client)
    frames = await _consume(
        generate_sse_events(ctx, user_message="¿estado?")
    )
    events = [_parse_event(f) for f in frames]
    kinds = [e[0] for e in events]
    assert "tool_use" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "turn_done"
    tu = next(p for k, p in events if k == "tool_use")
    assert tu["tool_name"] == "get_project_status"
    assert tu["tool_use_id"] == "toolu_abc"
    tr = next(p for k, p in events if k == "tool_result")
    assert tr["tool_use_id"] == "toolu_abc"
    assert tr["is_error"] is False
    assert tr["output"]["project"]["name"] == "proj-a"

    # Two stream calls happened (initial + tool round-trip).
    assert len(client.messages.calls) == 2


async def test_tool_roundtrip_limit(app_client) -> None:
    """Six consecutive tool_use events should surface TOOL_ROUNDTRIP_LIMIT."""
    from aether_api.services.chat.stream import (
        TOOL_ROUNDTRIP_LIMIT,
        generate_sse_events,
    )

    user_id, project_id, conv_id = await _seed_chat()
    client = _FakeClient()
    # Push 6 scripts each emitting a tool_use; the 6th must hit the limit.
    for i in range(TOOL_ROUNDTRIP_LIMIT + 1):
        client.messages.push(
            [
                _ev(
                    type="content_block_start",
                    content_block={
                        "type": "tool_use",
                        "id": f"toolu_{i}",
                        "name": "get_project_status",
                        "input": {},
                    },
                ),
                _ev(
                    type="message_delta",
                    stop_reason="tool_use",
                    usage=_Usage(input_tokens=10, output_tokens=5),
                ),
                _ev(type="message_stop"),
            ]
        )

    ctx = _make_ctx(user_id, project_id, conv_id, llm_client=client)
    frames = await _consume(generate_sse_events(ctx, user_message="loop"))
    events = [_parse_event(f) for f in frames]
    error_frame = next(p for k, p in events if k == "error")
    assert error_frame["code"] == "TOOL_ROUNDTRIP_LIMIT"

    # No turn_done after the limit hit (the design states partial
    # assistant message is saved, no turn_done).
    kinds = [k for k, _ in events]
    assert "turn_done" not in kinds


async def test_stream_interrupted_persists_partial(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_message_repository import ChatMessageRepository
    from aether_api.services.chat.stream import generate_sse_events

    user_id, project_id, conv_id = await _seed_chat()
    client = _FakeClient()
    # Make the very first call raise.
    client.messages.next_exception = RuntimeError("upstream blew up")

    ctx = _make_ctx(user_id, project_id, conv_id, llm_client=client)
    frames = await _consume(generate_sse_events(ctx, user_message="hi"))
    events = [_parse_event(f) for f in frames]
    kinds = [k for k, _ in events]
    assert "error" in kinds
    err = next(p for k, p in events if k == "error")
    assert err["code"] == "STREAM_INTERRUPTED"

    # Assistant row persisted with stop_reason='aborted'.
    maker = get_session_maker()
    async with maker() as session:
        repo = ChatMessageRepository(session)
        rows, _ = await repo.list_for_conversation(
            user_id=user_id, conversation_id=conv_id
        )
    assistant_rows = [r for r in rows if r.role == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0].stop_reason == "aborted"


async def test_cumulative_token_update_persisted(app_client) -> None:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )
    from aether_api.services.chat.stream import generate_sse_events

    user_id, project_id, conv_id = await _seed_chat()
    client = _FakeClient()
    client.messages.push(
        [
            _ev(type="text", text="hola"),
            _ev(
                type="message_delta",
                stop_reason="end_turn",
                usage=_Usage(input_tokens=1000, output_tokens=200),
            ),
            _ev(type="message_stop"),
        ]
    )
    ctx = _make_ctx(user_id, project_id, conv_id, llm_client=client)
    await _consume(generate_sse_events(ctx, user_message="ping"))

    maker = get_session_maker()
    async with maker() as session:
        conv = await ChatConversationRepository(session).get(
            user_id=user_id, conversation_id=conv_id
        )
    assert conv is not None
    # Stored as the sum tokens_in + tokens_out = 1200.
    assert conv.tokens_in_total == 1200
    assert float(conv.usd_estimated_total) > 0

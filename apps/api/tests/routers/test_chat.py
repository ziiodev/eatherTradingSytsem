"""End-to-end coverage for /api/projects/{id}/chat/* (Phase 4 of project-chat).

Covers all six endpoints. The Anthropic SDK is replaced by an in-process
fake — the same shape used by ``tests/services/chat/test_stream.py`` —
so no real network calls are issued.

Top-level invariants exercised:

* Auth gate (401 without cookie).
* Cross-tenant denial: 404 (NEVER 403) for all six endpoints.
* Cross-tenant write attempts emit a ``chat.cross_tenant_write_denied``
  audit row.
* 409 ``CHAT_TURN_IN_PROGRESS`` when an advisory lock is already held.
* 409 ``CHAT_BUDGET_EXCEEDED`` when the conversation's token rollup
  is at the 500k cap.
* 500 ``CHAT_NOT_CONFIGURED`` when the Anthropic API key is missing.
* Happy path: text-only turn → ``token`` + ``turn_done`` SSE frames.
* Tool round-trip: ``tool_use`` + ``tool_result`` SSE frames.
* Tool round-trip limit: 6th tool_use surfaces ``TOOL_ROUNDTRIP_LIMIT``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from aether_api.auth.cookies import CSRF_COOKIE

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fake Anthropic SDK — see tests/services/chat/test_stream.py for the shape.
# ---------------------------------------------------------------------------


class _Usage(dict):
    @property
    def input_tokens(self) -> int:
        return int(self.get("input_tokens", 0) or 0)

    @property
    def output_tokens(self) -> int:
        return int(self.get("output_tokens", 0) or 0)


def _ev(**kwargs: Any) -> dict[str, Any]:
    return kwargs


class _FakeStreamCM:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = list(events)

    async def __aenter__(self):
        async def _iter():
            for e in self._events:
                yield e

        return _iter()

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeMessages:
    def __init__(self) -> None:
        self.scripts: list[list[dict[str, Any]]] = []
        self.calls: list[dict[str, Any]] = []

    def push(self, events: list[dict[str, Any]]) -> None:
        self.scripts.append(events)

    def stream(self, **kwargs: Any) -> _FakeStreamCM:
        self.calls.append(kwargs)
        if not self.scripts:
            return _FakeStreamCM([])
        return _FakeStreamCM(self.scripts.pop(0))


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_and_login(
    client,
    *,
    email: str = "ops@example.com",
    password: str = "correct horse battery staple",
):
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_user

    maker = get_session_maker()
    async with maker() as session:
        user = await seed_user(session, email=email, password=password)
        await session.commit()
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return user


def _csrf_headers(client) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE)
    assert token, "csrf cookie missing — did you log in first?"
    return {"X-CSRF-Token": token}


async def _seed_project(owner_user) -> uuid.UUID:
    from aether_api.db.session import get_session_maker

    from tests._helpers import seed_project

    maker = get_session_maker()
    async with maker() as session:
        # Refresh the owner inside this session to avoid a detached
        # instance — seed_project only reads owner.id which is safe.
        project = await seed_project(session, owner=owner_user, name=f"p-{uuid.uuid4().hex[:8]}")
        await session.commit()
        return project.id


async def _seed_conversation(user_id: uuid.UUID, project_id: uuid.UUID) -> uuid.UUID:
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    maker = get_session_maker()
    async with maker() as session:
        conv = await ChatConversationRepository(session).create(
            user_id=user_id, project_id=project_id, title="t"
        )
        await session.commit()
        return conv.id


async def _install_fake_anthropic(monkeypatch) -> _FakeClient:
    """Patch the router's anthropic builder + settings so the chat works."""
    from aether_api.core.settings import get_settings
    from aether_api.routers import chat as chat_router_mod

    fake = _FakeClient()
    monkeypatch.setattr(
        chat_router_mod, "_build_anthropic_client", lambda: fake
    )

    # Settings — make sure ``anthropic_api_key`` is set for the
    # config-guard. We stamp a placeholder onto the cached settings
    # without going through the env (validators would re-parse). We
    # mutate a fresh Settings.copy with the field set.
    from pydantic import SecretStr

    original = get_settings()
    object.__setattr__(original, "anthropic_api_key", SecretStr("test-key"))
    return fake


def _parse_sse(text_body: str) -> list[tuple[str, dict[str, Any]]]:
    """Split the SSE body into (event, data-json) pairs."""
    out: list[tuple[str, dict[str, Any]]] = []
    for chunk in text_body.split("\n\n"):
        if not chunk.strip():
            continue
        event: str | None = None
        data: str | None = None
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if event is not None and data is not None:
            out.append((event, json.loads(data)))
    return out


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_list_conversations_requires_auth(app_client) -> None:
    resp = await app_client.get(f"/api/projects/{uuid.uuid4()}/chat/conversations")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cross-tenant 404 — applies to all six endpoints
# ---------------------------------------------------------------------------


async def test_cross_tenant_404_list_conversations(app_client) -> None:
    a = await _seed_user_and_login(
        app_client, email="a@example.com", password="apasspasspass"
    )
    project_a = await _seed_project(a)

    # Log in as user B.
    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    await _seed_user_and_login(
        app_client, email="b@example.com", password="bpasspasspass"
    )

    resp = await app_client.get(f"/api/projects/{project_a}/chat/conversations")
    assert resp.status_code == 404


async def test_cross_tenant_404_create_conversation(app_client) -> None:
    a = await _seed_user_and_login(
        app_client, email="a@example.com", password="apasspasspass"
    )
    project_a = await _seed_project(a)

    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    await _seed_user_and_login(
        app_client, email="b@example.com", password="bpasspasspass"
    )

    resp = await app_client.post(
        f"/api/projects/{project_a}/chat/conversations",
        json={"title": "hijack"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404


async def test_cross_tenant_404_get_conversation(app_client) -> None:
    a = await _seed_user_and_login(
        app_client, email="a@example.com", password="apasspasspass"
    )
    project_a = await _seed_project(a)
    conv_a = await _seed_conversation(a.id, project_a)

    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    await _seed_user_and_login(
        app_client, email="b@example.com", password="bpasspasspass"
    )

    resp = await app_client.get(
        f"/api/projects/{project_a}/chat/conversations/{conv_a}"
    )
    assert resp.status_code == 404


async def test_cross_tenant_404_patch_conversation_with_audit(
    app_client, monkeypatch
) -> None:
    """Cross-tenant PATCH returns 404 AND lands an audit_log row."""
    # Enable audit log writes for this test.
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    from aether_api.core.settings import get_settings

    object.__setattr__(get_settings(), "audit_log_enabled", True)

    a = await _seed_user_and_login(
        app_client, email="a@example.com", password="apasspasspass"
    )
    project_a = await _seed_project(a)
    conv_a = await _seed_conversation(a.id, project_a)

    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    user_b = await _seed_user_and_login(
        app_client, email="b@example.com", password="bpasspasspass"
    )

    resp = await app_client.patch(
        f"/api/projects/{project_a}/chat/conversations/{conv_a}",
        json={"title": "hijacked"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404

    # Audit row landed for actor B.
    from aether_api.db.session import get_session_maker
    from aether_api.models.audit_log import AuditLog
    from sqlalchemy import select

    maker = get_session_maker()
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.user_id == user_b.id,
                        AuditLog.action == "chat.cross_tenant_write_denied",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) >= 1


async def test_cross_tenant_404_list_messages(app_client) -> None:
    a = await _seed_user_and_login(
        app_client, email="a@example.com", password="apasspasspass"
    )
    project_a = await _seed_project(a)
    conv_a = await _seed_conversation(a.id, project_a)

    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    await _seed_user_and_login(
        app_client, email="b@example.com", password="bpasspasspass"
    )

    resp = await app_client.get(
        f"/api/projects/{project_a}/chat/conversations/{conv_a}/messages"
    )
    assert resp.status_code == 404


async def test_cross_tenant_404_post_message_with_audit(
    app_client, monkeypatch
) -> None:
    """Cross-tenant POST to the streaming endpoint 404s AND audits."""
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    from aether_api.core.settings import get_settings

    object.__setattr__(get_settings(), "audit_log_enabled", True)

    a = await _seed_user_and_login(
        app_client, email="a@example.com", password="apasspasspass"
    )
    project_a = await _seed_project(a)
    conv_a = await _seed_conversation(a.id, project_a)

    await app_client.post("/api/auth/logout", headers=_csrf_headers(app_client))
    app_client.cookies.clear()
    user_b = await _seed_user_and_login(
        app_client, email="b@example.com", password="bpasspasspass"
    )

    resp = await app_client.post(
        f"/api/projects/{project_a}/chat/conversations/{conv_a}/messages",
        json={"content": "hijack"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 404

    from aether_api.db.session import get_session_maker
    from aether_api.models.audit_log import AuditLog
    from sqlalchemy import select

    maker = get_session_maker()
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.user_id == user_b.id,
                        AuditLog.action == "chat.cross_tenant_write_denied",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# CRUD happy path
# ---------------------------------------------------------------------------


async def test_create_and_list_conversation(app_client) -> None:
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)

    resp = await app_client.post(
        f"/api/projects/{project_id}/chat/conversations",
        json={"title": "primera"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "primera"
    conv_id = body["id"]

    resp = await app_client.get(f"/api/projects/{project_id}/chat/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == conv_id

    resp = await app_client.get(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}"
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["conversation"]["id"] == conv_id
    assert detail["messages"] == []


async def test_patch_conversation_rename_and_archive(app_client) -> None:
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)

    # Rename.
    resp = await app_client.patch(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}",
        json={"title": "renombrada"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "renombrada"

    # Archive.
    resp = await app_client.patch(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}",
        json={"archived": True},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None

    # Default list (archived=False) no longer shows it.
    resp = await app_client.get(f"/api/projects/{project_id}/chat/conversations")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # archived=true does.
    resp = await app_client.get(
        f"/api/projects/{project_id}/chat/conversations?archived=true"
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_patch_empty_body_400(app_client) -> None:
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)

    resp = await app_client.patch(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}",
        json={},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Streaming endpoint — config / budget / lock / happy path / tool / limit
# ---------------------------------------------------------------------------


async def test_post_message_500_when_not_configured(app_client, monkeypatch) -> None:
    """When ANTHROPIC_API_KEY is unset → 500 CHAT_NOT_CONFIGURED."""
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)

    from aether_api.core.settings import get_settings

    settings = get_settings()
    object.__setattr__(settings, "anthropic_api_key", None)

    resp = await app_client.post(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
        json={"content": "hola"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"]["code"] == "CHAT_NOT_CONFIGURED"


async def test_post_message_409_budget_exceeded(app_client, monkeypatch) -> None:
    """tokens_in_total ≥ 500_000 → 409 CHAT_BUDGET_EXCEEDED."""
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)
    await _install_fake_anthropic(monkeypatch)

    # Bump the conversation's running token total to the cap.
    from aether_api.db.session import get_session_maker
    from aether_api.repositories.chat_conversation_repository import (
        ChatConversationRepository,
    )

    maker = get_session_maker()
    async with maker() as session:
        await ChatConversationRepository(session).increment_tokens(
            user_id=user.id,
            conversation_id=conv_id,
            tokens_in_delta=500_000,
            usd_delta=0,
        )
        await session.commit()

    resp = await app_client.post(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
        json={"content": "hola"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "CHAT_BUDGET_EXCEEDED"


async def test_post_message_happy_path_sse(app_client, monkeypatch) -> None:
    """Text-only turn → token events + turn_done frame."""
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)
    fake = await _install_fake_anthropic(monkeypatch)
    fake.messages.push(
        [
            _ev(type="text", text="Hola, "),
            _ev(type="text", text="¿cómo va el proyecto?"),
            _ev(
                type="message_delta",
                stop_reason="end_turn",
                usage=_Usage(input_tokens=120, output_tokens=15),
            ),
            _ev(type="message_stop"),
        ]
    )

    resp = await app_client.post(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
        json={"content": "estado, por favor"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers.get("x-accel-buffering") == "no"

    events = _parse_sse(resp.text)
    kinds = [e[0] for e in events]
    assert kinds.count("token") == 2
    assert "turn_done" in kinds
    turn_done = next(p for k, p in events if k == "turn_done")
    assert turn_done["stop_reason"] == "end_turn"
    assert turn_done["tokens_in"] == 120
    assert turn_done["tokens_out"] == 15


async def test_post_message_tool_use_sse(app_client, monkeypatch) -> None:
    """Model invokes a tool — SSE emits tool_use + tool_result frames."""
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)
    fake = await _install_fake_anthropic(monkeypatch)
    fake.messages.push(
        [
            _ev(type="text", text="Consulto el estado. "),
            _ev(
                type="content_block_start",
                content_block={
                    "type": "tool_use",
                    "id": "toolu_001",
                    "name": "get_project_status",
                    "input": {},
                },
            ),
            _ev(
                type="message_delta",
                stop_reason="tool_use",
                usage=_Usage(input_tokens=200, output_tokens=10),
            ),
            _ev(type="message_stop"),
        ]
    )
    fake.messages.push(
        [
            _ev(type="text", text="El proyecto está activo."),
            _ev(
                type="message_delta",
                stop_reason="end_turn",
                usage=_Usage(input_tokens=80, output_tokens=8),
            ),
            _ev(type="message_stop"),
        ]
    )

    resp = await app_client.post(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
        json={"content": "estado"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    kinds = [e[0] for e in events]
    assert "tool_use" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "turn_done"


async def test_post_message_tool_roundtrip_limit_error(app_client, monkeypatch) -> None:
    """A 6th tool_use surfaces error code TOOL_ROUNDTRIP_LIMIT."""
    from aether_api.services.chat.stream import TOOL_ROUNDTRIP_LIMIT

    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)
    fake = await _install_fake_anthropic(monkeypatch)
    for i in range(TOOL_ROUNDTRIP_LIMIT + 1):
        fake.messages.push(
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
                    usage=_Usage(input_tokens=5, output_tokens=2),
                ),
                _ev(type="message_stop"),
            ]
        )

    resp = await app_client.post(
        f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
        json={"content": "loop"},
        headers=_csrf_headers(app_client),
    )
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    err = next(p for k, p in events if k == "error")
    assert err["code"] == "TOOL_ROUNDTRIP_LIMIT"
    kinds = [k for k, _ in events]
    assert "turn_done" not in kinds


async def test_post_message_409_turn_in_progress(app_client, monkeypatch) -> None:
    """Two concurrent POSTs on the same conversation → second sees 409 CHAT_TURN_IN_PROGRESS.

    We block the first request inside the stream by inserting a sentinel
    that keeps the stream generator (and thus the advisory lock) alive
    until the second request has had a chance to fail.
    """
    user = await _seed_user_and_login(app_client)
    project_id = await _seed_project(user)
    conv_id = await _seed_conversation(user.id, project_id)
    fake = await _install_fake_anthropic(monkeypatch)

    # First turn — a single text event followed by a delay we control
    # by withholding the rest of the script.
    block = asyncio.Event()
    release = asyncio.Event()

    original_stream = fake.messages.stream

    def _slow_stream(**kwargs: Any) -> _FakeStreamCM:
        events = [
            _ev(type="text", text="esperando..."),
            _ev(
                type="message_delta",
                stop_reason="end_turn",
                usage=_Usage(input_tokens=10, output_tokens=2),
            ),
            _ev(type="message_stop"),
        ]

        class _SlowCM:
            async def __aenter__(self_inner):
                block.set()  # signal "stream started"
                await release.wait()  # block until test releases

                async def _iter():
                    for e in events:
                        yield e

                return _iter()

            async def __aexit__(self_inner, *args: Any) -> bool:
                return False

        return _SlowCM()

    fake.messages.stream = _slow_stream  # type: ignore[assignment]

    csrf = _csrf_headers(app_client)

    async def _first_request():
        return await app_client.post(
            f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
            json={"content": "primero"},
            headers=csrf,
        )

    first_task = asyncio.create_task(_first_request())
    try:
        # Wait until the first stream has started → lock is held.
        await asyncio.wait_for(block.wait(), timeout=5.0)

        # Second concurrent POST → should be refused immediately.
        resp = await app_client.post(
            f"/api/projects/{project_id}/chat/conversations/{conv_id}/messages",
            json={"content": "segundo"},
            headers=csrf,
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "CHAT_TURN_IN_PROGRESS"
    finally:
        release.set()
        # Drain the first request — must complete cleanly.
        first_resp = await first_task
        assert first_resp.status_code == 200
        # Restore the original stream callable so we don't leak the
        # patched method into other tests in the module.
        fake.messages.stream = original_stream  # type: ignore[assignment]

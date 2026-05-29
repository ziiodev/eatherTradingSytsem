"""Phase 11 of ``sdd/sleep-learning-loop`` — cross-tenant audit log.

Pinned behavioural contract:

* :func:`log_cross_tenant_attempt` emits a structured WARN log record
  ``aether.learning.cross_tenant_write_denied`` with the actor /
  target / table / operation fields. Payload is NEVER included.
* The first attempt for any new ``actor_user_id`` always logs (the
  bucket starts full).
* The bucket holds ``settings.learning_audit_rate_capacity`` tokens
  (default 10) and refills at ``capacity / window`` tokens/second.
  15 rapid attempts in <60 s ⇒ 10 logs + 5 silently dropped.
* The PermissionError contract is **independent** of the audit layer —
  the repository raises regardless of whether the audit log fires.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest


@pytest.fixture(autouse=True)
def _reset_audit_state() -> None:
    """The audit module holds process-global token buckets — reset
    before AND after every test so a 10-attempt burst from one case
    doesn't taint the next."""
    from aether_api.learning.audit import reset_for_test

    reset_for_test()
    yield
    reset_for_test()


# ---------------------------------------------------------------------------
# (a) Single attempt → single WARN with the right fields.
# ---------------------------------------------------------------------------


async def test_single_attempt_logs_structured_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aether_api.learning.audit import (
        AUDIT_LOG_KEY,
        log_cross_tenant_attempt,
    )

    caplog.set_level(logging.WARNING, logger="aether_api.learning.audit")

    actor = uuid.uuid4()
    target = uuid.uuid4()
    emitted = await log_cross_tenant_attempt(
        actor_user_id=actor,
        target_project_id=target,
        table_name="q_tables",
        operation="insert_version",
    )
    assert emitted is True

    matches = [r for r in caplog.records if r.message == AUDIT_LOG_KEY]
    assert len(matches) == 1
    record = matches[0]
    assert record.levelno == logging.WARNING
    assert record.actor_user_id == str(actor)
    assert record.target_project_id == str(target)
    assert record.table_name == "q_tables"
    assert record.operation == "insert_version"


# ---------------------------------------------------------------------------
# (b) Rate limit — 15 attempts ⇒ 10 logs + 5 drops.
# ---------------------------------------------------------------------------


async def test_rate_limit_drops_after_capacity(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Default capacity is 10 — the 11th–15th attempts inside the
    refill window MUST silently drop their log line."""
    from aether_api.core.settings import get_settings
    from aether_api.learning.audit import (
        AUDIT_LOG_KEY,
        log_cross_tenant_attempt,
    )

    # Lock defaults so the test is independent of any future tuning.
    monkeypatch.setenv("LEARNING_AUDIT_RATE_CAPACITY", "10")
    monkeypatch.setenv("LEARNING_AUDIT_RATE_WINDOW_SECONDS", "60")
    get_settings.cache_clear()

    caplog.set_level(logging.WARNING, logger="aether_api.learning.audit")

    actor = uuid.uuid4()
    results = []
    for _ in range(15):
        emitted = await log_cross_tenant_attempt(
            actor_user_id=actor,
            target_project_id=uuid.uuid4(),
            table_name="episodic_memory",
            operation="insert",
        )
        results.append(emitted)

    # Exactly 10 logged, 5 dropped.
    assert results.count(True) == 10
    assert results.count(False) == 5
    matches = [r for r in caplog.records if r.message == AUDIT_LOG_KEY]
    assert len(matches) == 10


# ---------------------------------------------------------------------------
# (c) Bucket starts full — the very first attempt always logs.
# ---------------------------------------------------------------------------


async def test_fresh_actor_first_attempt_always_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aether_api.learning.audit import log_cross_tenant_attempt

    caplog.set_level(logging.WARNING, logger="aether_api.learning.audit")
    actor = uuid.uuid4()
    emitted = await log_cross_tenant_attempt(
        actor_user_id=actor,
        target_project_id=uuid.uuid4(),
        table_name="semantic_memory",
        operation="insert",
    )
    assert emitted is True


# ---------------------------------------------------------------------------
# (d) Buckets are per-actor — actor A exhausting its bucket does NOT
#     starve actor B.
# ---------------------------------------------------------------------------


async def test_buckets_are_isolated_per_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aether_api.core.settings import get_settings
    from aether_api.learning.audit import log_cross_tenant_attempt

    monkeypatch.setenv("LEARNING_AUDIT_RATE_CAPACITY", "10")
    monkeypatch.setenv("LEARNING_AUDIT_RATE_WINDOW_SECONDS", "60")
    get_settings.cache_clear()

    actor_a = uuid.uuid4()
    actor_b = uuid.uuid4()

    for _ in range(15):
        await log_cross_tenant_attempt(
            actor_user_id=actor_a,
            target_project_id=uuid.uuid4(),
            table_name="q_tables",
            operation="insert_version",
        )

    # A is now exhausted; B should still have a full bucket.
    emitted = await log_cross_tenant_attempt(
        actor_user_id=actor_b,
        target_project_id=uuid.uuid4(),
        table_name="q_tables",
        operation="insert_version",
    )
    assert emitted is True


# ---------------------------------------------------------------------------
# (e) Bucket refills after the window expires.
# ---------------------------------------------------------------------------


async def test_bucket_refills_after_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set a 0.05s window so the test can simply ``await asyncio.sleep``
    past it. Production windows are 60s — same logic, longer scale."""
    from aether_api.core.settings import get_settings
    from aether_api.learning.audit import log_cross_tenant_attempt

    monkeypatch.setenv("LEARNING_AUDIT_RATE_CAPACITY", "3")
    monkeypatch.setenv("LEARNING_AUDIT_RATE_WINDOW_SECONDS", "0.05")
    get_settings.cache_clear()

    actor = uuid.uuid4()

    # Empty the bucket.
    for _ in range(3):
        await log_cross_tenant_attempt(
            actor_user_id=actor,
            target_project_id=uuid.uuid4(),
            table_name="q_tables",
            operation="insert_version",
        )
    # 4th attempt is dropped (bucket empty).
    dropped = await log_cross_tenant_attempt(
        actor_user_id=actor,
        target_project_id=uuid.uuid4(),
        table_name="q_tables",
        operation="insert_version",
    )
    assert dropped is False

    # Sleep past one full window — bucket fully refills.
    await asyncio.sleep(0.07)
    refilled = await log_cross_tenant_attempt(
        actor_user_id=actor,
        target_project_id=uuid.uuid4(),
        table_name="q_tables",
        operation="insert_version",
    )
    assert refilled is True


# ---------------------------------------------------------------------------
# (f) Payload-free — audit MUST NOT carry Q-Table data, episode payload,
#     or anything beyond the four identifier fields.
# ---------------------------------------------------------------------------


async def test_payload_never_included(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from aether_api.learning.audit import (
        AUDIT_LOG_KEY,
        log_cross_tenant_attempt,
    )

    caplog.set_level(logging.WARNING, logger="aether_api.learning.audit")
    await log_cross_tenant_attempt(
        actor_user_id=uuid.uuid4(),
        target_project_id=uuid.uuid4(),
        table_name="q_tables",
        operation="insert_version",
    )
    record = [r for r in caplog.records if r.message == AUDIT_LOG_KEY][-1]
    # The structured extra MUST contain exactly the four pinned fields —
    # NOT, for example, an unintended `payload` / `table_data` /
    # `reward` key.
    forbidden = {"payload", "table_data", "reward", "state", "meta_data"}
    record_dict = vars(record)
    assert not (forbidden & set(record_dict.keys()))


# ---------------------------------------------------------------------------
# (g) Repository wiring — the four learning-table writers MUST call the
#     audit log on every cross-tenant refusal.
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_repository_cross_tenant_writes_emit_audit(
    app_client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drive each of the four write paths with a foreign ``user_id`` and
    assert the audit log fires for each one. Pins the wiring layer —
    the unit tests above pin :func:`log_cross_tenant_attempt` itself.
    """
    from decimal import Decimal

    from aether_api.db.session import get_session_maker
    from aether_api.learning.audit import AUDIT_LOG_KEY
    from aether_api.repositories.episodic_memory_repository import (
        EpisodicMemoryRepository,
    )
    from aether_api.repositories.q_table_repository import QTableRepository
    from aether_api.repositories.semantic_memory_repository import (
        SemanticMemoryRepository,
    )

    from tests._helpers import seed_project, seed_user

    maker = get_session_maker()
    async with maker() as session:
        user_a = await seed_user(
            session,
            email=f"audit-a-{uuid.uuid4().hex[:8]}@example.com",
            password="correct horse battery staple",
        )
        user_b = await seed_user(
            session,
            email=f"audit-b-{uuid.uuid4().hex[:8]}@example.com",
            password="correct horse battery staple",
        )
        project_a = await seed_project(
            session, owner=user_a, name=f"audit-{uuid.uuid4().hex[:8]}"
        )
        await session.commit()

    caplog.set_level(logging.WARNING, logger="aether_api.learning.audit")

    async with maker() as session:
        qt_repo = QTableRepository(session)
        epi_repo = EpisodicMemoryRepository(session)
        sem_repo = SemanticMemoryRepository(session)

        # q_tables.insert_version
        with pytest.raises(PermissionError):
            await qt_repo.insert_version(
                user_id=user_b.id,
                project_id=project_a.id,
                version=1,
                table_data={},
                learning_rate=Decimal("0.150"),
                discount_factor=Decimal("0.920"),
                metadata={},
            )

        # episodic_memory.insert
        with pytest.raises(PermissionError):
            await epi_repo.insert(
                user_id=user_b.id,
                project_id=project_a.id,
                trade_id=None,
                state={"k": 1},
                state_key="sk:audit",
                action="buy",
                reward=Decimal("0.0"),
                result=None,
                worker_reasoning=None,
                q_value_before=None,
                q_value_after=None,
                is_special=False,
            )

        # episodic_memory.mark_special
        with pytest.raises(PermissionError):
            await epi_repo.mark_special(
                user_id=user_b.id,
                project_id=project_a.id,
                episode_ids=[uuid.uuid4()],
            )

        # semantic_memory.insert
        with pytest.raises(PermissionError):
            await sem_repo.insert(
                user_id=user_b.id,
                project_id=project_a.id,
                rule_type="rule",
                title="t",
                content="c",
                confidence=0.5,
                source="test",
            )

    # 4 attempts, all logged (bucket capacity ≥ 4 by default).
    matches = [r for r in caplog.records if r.message == AUDIT_LOG_KEY]
    tables_logged = {m.table_name for m in matches}
    assert tables_logged == {
        "q_tables",
        "episodic_memory",
        "semantic_memory",
    }
    operations_logged = {m.operation for m in matches}
    assert "insert_version" in operations_logged
    assert "mark_special" in operations_logged
    assert "insert" in operations_logged
    # All recorded the foreign actor.
    actors_logged = {m.actor_user_id for m in matches}
    assert actors_logged == {str(user_b.id)}

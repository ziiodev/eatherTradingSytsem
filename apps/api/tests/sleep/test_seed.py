"""Tests for :mod:`aether_api.sleep.seed`.

Covers the four contractual paths:

* **Empty target tables, doc files present** → three rows inserted per user.
* **Re-run with no changes** → idempotent no-op (``unchanged == 3``).
* **Doc body changed AND the previous hash is registered as a known
  seed hash** → row body updates and ``version`` bumps.
* **Operator-edited row** (its current hash is NOT in
  :data:`KNOWN_SEED_HASHES`) → row left untouched, WARN logged,
  ``skipped_operator_edited == 1``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@pytest.fixture
def isolated_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point :mod:`aether_api.sleep.seed` at a temp docs dir.

    We rewrite the module-level :data:`SEED_PROMPTS` tuple so each
    prompt's ``path`` lives under ``tmp_path``. The originals are
    restored on teardown via monkeypatch.
    """
    from aether_api.sleep import seed as seed_mod

    fake_dir = tmp_path / "docs"
    fake_dir.mkdir()

    fake_micro = fake_dir / "FaseMicorSuenoWorker.md"
    fake_deep_w = fake_dir / "FaseSuenoWorker.md"
    fake_deep_s = fake_dir / "FasesSuenoAprendizajeProfundo.md"

    fake_micro.write_text("# micro v1\n\nbody micro 1\n", encoding="utf-8")
    fake_deep_w.write_text("# deep worker v1\n\nbody deep worker 1\n", encoding="utf-8")
    fake_deep_s.write_text("# deep system v1\n\nbody deep system 1\n", encoding="utf-8")

    new_prompts = (
        seed_mod._PromptDoc(  # noqa: SLF001 — test re-pins the doc map.
            name="sleep/micro-worker",
            title="Sueño - Micro (Worker)",
            path=fake_micro,
        ),
        seed_mod._PromptDoc(  # noqa: SLF001
            name="sleep/deep-worker",
            title="Sueño - Profundo (Worker)",
            path=fake_deep_w,
        ),
        seed_mod._PromptDoc(  # noqa: SLF001
            name="sleep/deep-system",
            title="Sueño - Profundo (Sistema)",
            path=fake_deep_s,
        ),
    )
    monkeypatch.setattr(seed_mod, "SEED_PROMPTS", new_prompts)
    seed_mod._reset_doc_cache()  # noqa: SLF001
    yield fake_dir
    seed_mod._reset_doc_cache()  # noqa: SLF001


async def _seed_one_user(session, email: str) -> uuid.UUID:
    from tests._helpers import seed_user

    user = await seed_user(session, email=email, password="correct horse battery staple")
    await session.commit()
    return user.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_seed_inserts_three_rows_on_empty_db(
    app_client, isolated_docs: Path
) -> None:
    """Empty ``skills`` table + 1 active user → 3 inserts."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.skill import SkillDefinition
    from aether_api.sleep.seed import seed_sleep_prompts
    from sqlalchemy import select

    maker = get_session_maker()
    async with maker() as session:
        user_id = await _seed_one_user(session, f"seed-{uuid.uuid4().hex[:8]}@example.com")

    async with maker() as session:
        result = await seed_sleep_prompts(session)

    assert result.inserted == 3
    assert result.updated == 0
    assert result.skipped_operator_edited == 0
    assert result.unchanged == 0
    assert result.errors == []

    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(SkillDefinition).where(SkillDefinition.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        names = {r.name for r in rows}
        assert names == {"sleep/micro-worker", "sleep/deep-worker", "sleep/deep-system"}
        for r in rows:
            assert r.runtime == "markdown"
            assert r.type == "analytic"
            assert r.is_active is True
            assert r.version == 1


async def test_seed_is_idempotent_on_rerun(
    app_client, isolated_docs: Path
) -> None:
    """Running the seed twice in a row is a no-op the second time."""
    from aether_api.db.session import get_session_maker
    from aether_api.sleep.seed import seed_sleep_prompts

    maker = get_session_maker()
    async with maker() as session:
        await _seed_one_user(session, f"seed-{uuid.uuid4().hex[:8]}@example.com")

    async with maker() as session:
        first = await seed_sleep_prompts(session)
    assert first.inserted == 3

    async with maker() as session:
        second = await seed_sleep_prompts(session)
    assert second.inserted == 0
    assert second.updated == 0
    assert second.skipped_operator_edited == 0
    assert second.unchanged == 3
    assert second.errors == []


async def test_seed_updates_when_doc_changes_and_old_hash_is_known(
    app_client, isolated_docs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator updates a doc + registers the OLD hash → row updates and bumps version."""
    from aether_api.db.session import get_session_maker
    from aether_api.models.skill import SkillDefinition
    from aether_api.sleep import seed as seed_mod
    from aether_api.sleep.seed import seed_sleep_prompts
    from sqlalchemy import select

    micro_path = isolated_docs / "FaseMicorSuenoWorker.md"
    old_body = micro_path.read_text(encoding="utf-8")
    old_hash = _hash(old_body)

    maker = get_session_maker()
    async with maker() as session:
        user_id = await _seed_one_user(session, f"seed-{uuid.uuid4().hex[:8]}@example.com")

    # First seed.
    async with maker() as session:
        await seed_sleep_prompts(session)

    # Operator changes the doc AND adds the old hash to the known set.
    new_body = "# micro v2\n\nbody micro REVISED\n"
    micro_path.write_text(new_body, encoding="utf-8")
    seed_mod._reset_doc_cache()  # noqa: SLF001 — force re-read.
    monkeypatch.setattr(
        seed_mod, "KNOWN_SEED_HASHES", frozenset({old_hash})
    )

    async with maker() as session:
        result = await seed_sleep_prompts(session)

    # 1 update for micro-worker; the other two are unchanged.
    assert result.updated == 1
    assert result.unchanged == 2
    assert result.skipped_operator_edited == 0
    assert result.inserted == 0
    assert result.errors == []

    async with maker() as session:
        row = (
            (
                await session.execute(
                    select(SkillDefinition).where(
                        SkillDefinition.user_id == user_id,
                        SkillDefinition.name == "sleep/micro-worker",
                    )
                )
            )
            .scalar_one()
        )
        assert row.code == new_body
        assert row.version == 2  # bumped from 1.


async def test_seed_leaves_operator_edits_untouched(
    app_client, isolated_docs: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """A row whose current body hash is NOT in KNOWN_SEED_HASHES is operator-owned."""
    import logging

    from aether_api.db.session import get_session_maker
    from aether_api.models.skill import SkillDefinition
    from aether_api.sleep import seed as seed_mod
    from aether_api.sleep.seed import seed_sleep_prompts
    from sqlalchemy import select

    maker = get_session_maker()
    async with maker() as session:
        user_id = await _seed_one_user(session, f"seed-{uuid.uuid4().hex[:8]}@example.com")

    # First seed populates the three rows.
    async with maker() as session:
        await seed_sleep_prompts(session)

    # Operator hand-edits sleep/micro-worker via the normal /api/skills
    # surface — modelled here as a direct row mutation. The new body
    # hash is NOT in KNOWN_SEED_HASHES.
    operator_body = "# operator override\n\nMy own micro-sleep instructions.\n"
    async with maker() as session:
        row = (
            (
                await session.execute(
                    select(SkillDefinition).where(
                        SkillDefinition.user_id == user_id,
                        SkillDefinition.name == "sleep/micro-worker",
                    )
                )
            )
            .scalar_one()
        )
        row.code = operator_body
        row.version = (row.version or 1) + 1
        await session.commit()

    # Now bump the doc on disk to a different body so the seeder
    # WOULD want to write — proving the operator-edited check stops it.
    micro_path = isolated_docs / "FaseMicorSuenoWorker.md"
    micro_path.write_text("# micro v2 from seeder\n", encoding="utf-8")
    seed_mod._reset_doc_cache()  # noqa: SLF001
    # KNOWN_SEED_HASHES intentionally empty — the operator's current
    # body must not appear in it.
    monkeypatch.setattr(seed_mod, "KNOWN_SEED_HASHES", frozenset())

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aether_api.sleep.seed"):
        async with maker() as session:
            result = await seed_sleep_prompts(session)

    assert result.skipped_operator_edited == 1
    assert result.updated == 0
    assert result.inserted == 0
    assert result.unchanged == 2

    # Operator's body must be intact.
    async with maker() as session:
        row = (
            (
                await session.execute(
                    select(SkillDefinition).where(
                        SkillDefinition.user_id == user_id,
                        SkillDefinition.name == "sleep/micro-worker",
                    )
                )
            )
            .scalar_one()
        )
        assert row.code == operator_body

    # WARN must mention the slug. Match loosely on substring so tweaks
    # to the log format don't break the test.
    warn_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("sleep/micro-worker" in m for m in warn_msgs), warn_msgs

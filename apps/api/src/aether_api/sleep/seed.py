"""Seed the three canonical Sleep Phase prompt skills from ``docs/`` at boot.

The Sleep Phase consumes three operator-editable prompts:

* ``sleep/micro-worker``  — Worker's micro-sleep reflection (short pause).
* ``sleep/deep-worker``   — Worker's deep-sleep reflection.
* ``sleep/deep-system``   — System-wide deep-sleep learning pass.

These prompts live as **plain markdown files in the repo** (``docs/``) so
operators can edit them without touching Python. On every boot we
upsert them into the ``skills`` table as ``runtime='markdown'`` rows
(see :mod:`aether_api.models.skill`).

Schema-fit reality check
------------------------

The ``skills`` table — fixed by the ``skills-api`` canonical spec
(#2032) and migration ``0003_skills`` — has neither a ``slug`` nor a
``source`` column. To distinguish a row that the seeder owns from one
that an operator has edited we use **content-hash provenance**:

* The seeder maintains an in-module set
  :data:`KNOWN_SEED_HASHES` containing the SHA-256 of every body the
  seeder has ever written for these three skills (current + history).
* On boot, for each target skill identified by ``(user_id, name)``:
    - **Absent** → INSERT.
    - **Present and current hash ∈ KNOWN_SEED_HASHES** → seed-owned.
      Update body and bump ``version`` iff the new body differs.
    - **Present and current hash ∉ KNOWN_SEED_HASHES** → operator
      edited. Leave row untouched, log a WARN, count it as
      ``skipped_operator_edited``.

Names are used as the slug analog (``sleep/micro-worker`` etc.). They
fit the ``VARCHAR(100)`` width with room to spare and are stable
identifiers per user.

Tenant fit
----------

``skills.user_id`` is ``NOT NULL``; there is no system tenant. The
seeder writes ONE copy per active user (``users.is_active = true``).
New tenants pick up the seeds on the next boot — the function is
fully idempotent across both runs and users.

File reads are lazy and cached at module level so operators editing
the markdown only require an API restart (not a code rebuild) to roll
out the change. After an edit, the next boot computes a new
content_hash, finds the previous one in :data:`KNOWN_SEED_HASHES` if
maintainers added it there (recommended workflow: append the OLD hash
to the set in the same commit that updates the doc), and seamlessly
upgrades the row.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.models.skill import SkillDefinition
from aether_api.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document map — slug → (filesystem path, human title, skill type).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _find_repo_root() -> Path:
    """Walk upward from this file until we find the repo root.

    The repo root is identified as the first ancestor directory that
    contains BOTH an ``apps/`` sub-directory AND a ``docs/`` sub-directory.
    This is robust to the file moving deeper in the tree and to
    invocations from arbitrary working directories.

    Cached so we only walk the filesystem once per process.

    Raises:
        RuntimeError: if no ancestor matches both markers — that means
            the source tree has been reshaped or the file was copied
            outside the repo, neither of which is a recoverable state.
    """
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "apps").is_dir() and (candidate / "docs").is_dir():
            return candidate
    raise RuntimeError(
        f"sleep.seed: could not locate repo root from {here!s}; "
        "expected an ancestor containing both 'apps/' and 'docs/'."
    )


_REPO_ROOT: Final[Path] = _find_repo_root()
_DOCS_DIR: Final[Path] = _REPO_ROOT / "docs"


@dataclass(frozen=True)
class _PromptDoc:
    """A single seed prompt definition.

    ``name`` doubles as the slug — it is what the ``skills.name`` column
    stores and what we look the row up by. ``type`` is fixed to
    ``analytic`` because the prompts are decision-frameworks (the
    closest fit in the ``skills_type_valid`` CHECK constraint — see
    :data:`aether_api.models.skill.SKILL_TYPES`).
    """

    name: str
    title: str
    path: Path
    type: str = "analytic"


SEED_PROMPTS: Final[tuple[_PromptDoc, ...]] = (
    _PromptDoc(
        name="sleep/micro-worker",
        title="Sueño - Micro (Worker)",
        path=_DOCS_DIR / "FaseMicorSuenoWorker.md",
    ),
    _PromptDoc(
        name="sleep/deep-worker",
        title="Sueño - Profundo (Worker)",
        path=_DOCS_DIR / "FaseSuenoWorker.md",
    ),
    _PromptDoc(
        name="sleep/deep-system",
        title="Sueño - Profundo (Sistema)",
        path=_DOCS_DIR / "FasesSuenoAprendizajeProfundo.md",
    ),
)


# ---------------------------------------------------------------------------
# Seed provenance — historical content hashes the seeder is allowed to
# overwrite. Append (never remove) old hashes here every time the
# corresponding doc file changes so existing seed-owned rows continue
# to upgrade smoothly on the next boot.
# ---------------------------------------------------------------------------

KNOWN_SEED_HASHES: Final[frozenset[str]] = frozenset(
    {
        # Bootstrap entries — replaced/extended whenever a doc body
        # changes. Compute via:
        #   hashlib.sha256(Path("docs/FaseXxx.md").read_bytes()).hexdigest()
        # then commit the hash AND the doc body in the same change.
    }
)


# ---------------------------------------------------------------------------
# In-process doc cache. Keyed by absolute path; value is (mtime_ns, body).
# Boot rereads after a doc edit because mtime changes; tests can clear
# via :func:`_reset_doc_cache`.
# ---------------------------------------------------------------------------

_DocCacheEntry = tuple[int, str]
_doc_cache: dict[Path, _DocCacheEntry] = {}


def _reset_doc_cache() -> None:
    """Test hook — clear the doc cache so the next read hits disk."""
    _doc_cache.clear()


def _read_doc(path: Path) -> str:
    """Read ``path`` from disk, caching by ``(path, mtime_ns)``.

    Mtime-keyed so a doc edited between two boots (or even within the
    same Python process, e.g. tests) is re-read transparently.
    """
    stat = path.stat()
    cached = _doc_cache.get(path)
    if cached is not None and cached[0] == stat.st_mtime_ns:
        return cached[1]
    body = path.read_text(encoding="utf-8")
    _doc_cache[path] = (stat.st_mtime_ns, body)
    return body


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Result envelope.
# ---------------------------------------------------------------------------


@dataclass
class SeedResult:
    """Summary of one :func:`seed_sleep_prompts` invocation.

    Counts are summed across users — three docs × N users.
    """

    inserted: int = 0
    updated: int = 0
    skipped_operator_edited: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------


async def seed_sleep_prompts(session: AsyncSession) -> SeedResult:
    """Idempotently seed the three Sleep Phase prompt skills.

    For every active user, ensure a markdown skill row exists for each
    of :data:`SEED_PROMPTS`. The seeder owns rows whose body hash is
    listed in :data:`KNOWN_SEED_HASHES`; rows hashing to anything else
    were operator-edited and are left untouched.

    The function commits its own work and returns a :class:`SeedResult`.
    Per-user / per-doc failures are caught individually so a single
    bad file (e.g. missing on disk) never blocks the rest.
    """
    result = SeedResult()

    # ----- Load doc bodies once. A missing file is an error but does
    # not abort the whole run; the other prompts still seed.
    doc_bodies: dict[str, str] = {}
    doc_hashes: dict[str, str] = {}
    for doc in SEED_PROMPTS:
        try:
            body = _read_doc(doc.path)
        except OSError as exc:
            msg = f"seed.read_failed name={doc.name} path={doc.path} err={exc}"
            logger.warning("sleep.seed: %s", msg)
            result.errors.append(msg)
            continue
        doc_bodies[doc.name] = body
        doc_hashes[doc.name] = _hash(body)

    if not doc_bodies:
        logger.warning("sleep.seed: no doc bodies loaded; nothing to seed")
        return result

    # ----- Enumerate active users. New users picked up on next boot.
    users_stmt = select(User.id).where(User.is_active.is_(True))
    user_ids: list[uuid.UUID] = list((await session.execute(users_stmt)).scalars().all())
    if not user_ids:
        logger.info("sleep.seed: no active users; nothing to seed yet")
        return result

    for user_id in user_ids:
        for doc in SEED_PROMPTS:
            body_opt: str | None = doc_bodies.get(doc.name)
            hash_opt: str | None = doc_hashes.get(doc.name)
            if body_opt is None or hash_opt is None:
                # A read error above already recorded the failure.
                continue
            try:
                await _upsert_one(
                    session,
                    user_id=user_id,
                    doc=doc,
                    body=body_opt,
                    new_hash=hash_opt,
                    result=result,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort per pair.
                await session.rollback()
                msg = f"seed.upsert_failed user_id={user_id} name={doc.name} err={exc}"
                logger.warning("sleep.seed: %s", msg)
                result.errors.append(msg)

    await session.commit()
    logger.info(
        "sleep.seed: inserted=%d updated=%d skipped_operator_edited=%d unchanged=%d errors=%d",
        result.inserted,
        result.updated,
        result.skipped_operator_edited,
        result.unchanged,
        len(result.errors),
    )
    return result


async def _upsert_one(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    doc: _PromptDoc,
    body: str,
    new_hash: str,
    result: SeedResult,
) -> None:
    """Perform the absent / seed-owned / operator-edited branch for ONE row."""
    stmt = select(SkillDefinition).where(
        SkillDefinition.user_id == user_id,
        SkillDefinition.name == doc.name,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    if existing is None:
        # INSERT — brand-new seed row.
        new_row = SkillDefinition(
            user_id=user_id,
            name=doc.name,
            type=doc.type,
            description=doc.title,
            code=body,
            runtime="markdown",
            input_signature={},
            output_signature={},
            is_active=True,
        )
        session.add(new_row)
        await session.flush()
        result.inserted += 1
        return

    current_hash = _hash(existing.code or "")

    if current_hash == new_hash:
        # Already up to date — true no-op idempotent case.
        result.unchanged += 1
        return

    if current_hash not in KNOWN_SEED_HASHES:
        # Operator has edited this row (its current body hash does not
        # match any historical seed). Do NOT overwrite.
        logger.warning(
            "sleep.seed: skipped_operator_edited user_id=%s name=%s "
            "current_hash=%s (not in KNOWN_SEED_HASHES)",
            user_id,
            doc.name,
            current_hash,
        )
        result.skipped_operator_edited += 1
        return

    # Seed-owned row with stale body → UPDATE + bump version.
    existing.code = body
    existing.description = doc.title
    existing.runtime = "markdown"
    existing.version = (existing.version or 1) + 1
    await session.flush()
    result.updated += 1


__all__ = [
    "KNOWN_SEED_HASHES",
    "SEED_PROMPTS",
    "SeedResult",
    "seed_sleep_prompts",
]

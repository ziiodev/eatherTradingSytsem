"""SQLite-backed state store for registered EAs and run history.

Schema (version 1):

    eas(
        ea_id        TEXT PRIMARY KEY,    -- stable opaque handle (slug)
        ea_name      TEXT NOT NULL UNIQUE,
        source_path  TEXT NOT NULL,       -- path inside MQL5/Experts/managed/
        sha256       TEXT NOT NULL,       -- of the .mq5 source
        created_at   TEXT NOT NULL,       -- ISO-8601 UTC
        updated_at   TEXT NOT NULL
    )

    runs(
        run_id       TEXT PRIMARY KEY,    -- ULID-ish slug
        ea_id        TEXT NOT NULL REFERENCES eas(ea_id) ON DELETE CASCADE,
        kind         TEXT NOT NULL CHECK (kind IN ('compile','backtest','optimize')),
        status       TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','cancelled')),
        created_at   TEXT NOT NULL,
        started_at   TEXT,
        finished_at  TEXT,
        symbol       TEXT,
        period       TEXT,
        from_date    TEXT,
        to_date      TEXT,
        artifacts    TEXT NOT NULL DEFAULT '{}',  -- JSON
        summary      TEXT NOT NULL DEFAULT '{}',  -- JSON
        error_code   TEXT,
        error_msg    TEXT
    )

    schema_version(version INTEGER PRIMARY KEY)
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ErrorCode, StateError

SCHEMA_VERSION = 1

_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eas (
        ea_id        TEXT PRIMARY KEY,
        ea_name      TEXT NOT NULL UNIQUE,
        source_path  TEXT NOT NULL,
        sha256       TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id       TEXT PRIMARY KEY,
        ea_id        TEXT NOT NULL REFERENCES eas(ea_id) ON DELETE CASCADE,
        kind         TEXT NOT NULL CHECK (kind IN ('compile','backtest','optimize')),
        status       TEXT NOT NULL CHECK (status IN ('queued','running','done','failed','cancelled')),
        created_at   TEXT NOT NULL,
        started_at   TEXT,
        finished_at  TEXT,
        symbol       TEXT,
        period       TEXT,
        from_date    TEXT,
        to_date      TEXT,
        artifacts    TEXT NOT NULL DEFAULT '{}',
        summary      TEXT NOT NULL DEFAULT '{}',
        error_code   TEXT,
        error_msg    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_ea_id ON runs(ea_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_kind ON runs(kind)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at)",
)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True, slots=True)
class EARecord:
    ea_id: str
    ea_name: str
    source_path: str
    sha256: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    ea_id: str
    kind: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    symbol: str | None = None
    period: str | None = None
    from_date: str | None = None
    to_date: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_msg: str | None = None


class StateStore:
    """Thin wrapper around a single SQLite database file.

    Not thread-safe by itself; callers must serialise access via the
    workspace lock or an asyncio queue (see design).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()

    # ------------------------------------------------------------------ schema

    def _init_schema(self) -> None:
        with self._tx() as cur:
            for ddl in _DDL_STATEMENTS:
                cur.execute(ddl)
            row = cur.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif row["version"] != SCHEMA_VERSION:
                raise StateError(
                    ErrorCode.STATE_CORRUPTED,
                    f"Unsupported state schema version: {row['version']} (expected {SCHEMA_VERSION})",
                )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN")
            yield cur
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------ EA CRUD

    def upsert_ea(
        self,
        *,
        ea_id: str,
        ea_name: str,
        source_path: str,
        sha256: str,
    ) -> EARecord:
        now = _utcnow_iso()
        with self._tx() as cur:
            existing = cur.execute(
                "SELECT created_at FROM eas WHERE ea_id = ?",
                (ea_id,),
            ).fetchone()
            if existing is None:
                cur.execute(
                    """INSERT INTO eas(ea_id, ea_name, source_path, sha256, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                    (ea_id, ea_name, source_path, sha256, now, now),
                )
                created_at = now
            else:
                cur.execute(
                    """UPDATE eas SET ea_name = ?, source_path = ?, sha256 = ?, updated_at = ?
                           WHERE ea_id = ?""",
                    (ea_name, source_path, sha256, now, ea_id),
                )
                created_at = existing["created_at"]
        return EARecord(
            ea_id=ea_id,
            ea_name=ea_name,
            source_path=source_path,
            sha256=sha256,
            created_at=created_at,
            updated_at=now,
        )

    def get_ea(self, ea_id: str) -> EARecord | None:
        row = self._conn.execute(
            "SELECT * FROM eas WHERE ea_id = ?", (ea_id,)
        ).fetchone()
        return _row_to_ea(row) if row else None

    def get_ea_by_name(self, ea_name: str) -> EARecord | None:
        row = self._conn.execute(
            "SELECT * FROM eas WHERE ea_name = ?", (ea_name,)
        ).fetchone()
        return _row_to_ea(row) if row else None

    def list_eas(self) -> list[EARecord]:
        rows = self._conn.execute(
            "SELECT * FROM eas ORDER BY created_at ASC"
        ).fetchall()
        return [_row_to_ea(r) for r in rows]

    def delete_ea(self, ea_id: str) -> bool:
        with self._tx() as cur:
            cur.execute("DELETE FROM eas WHERE ea_id = ?", (ea_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------ run CRUD

    def create_run(
        self,
        *,
        run_id: str,
        ea_id: str,
        kind: str,
        symbol: str | None = None,
        period: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> RunRecord:
        now = _utcnow_iso()
        record = RunRecord(
            run_id=run_id,
            ea_id=ea_id,
            kind=kind,
            status="queued",
            created_at=now,
            symbol=symbol,
            period=period,
            from_date=from_date,
            to_date=to_date,
        )
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO runs(run_id, ea_id, kind, status, created_at,
                                    symbol, period, from_date, to_date,
                                    artifacts, summary)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    ea_id,
                    kind,
                    "queued",
                    now,
                    symbol,
                    period,
                    from_date,
                    to_date,
                    "{}",
                    "{}",
                ),
            )
        return record

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        artifacts: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_msg: str | None = None,
    ) -> RunRecord:
        with self._tx() as cur:
            row = cur.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise StateError(
                    ErrorCode.RUN_NOT_FOUND,
                    f"run {run_id!r} not found",
                )

            new_status = status if status is not None else row["status"]
            new_started = started_at if started_at is not None else row["started_at"]
            new_finished = finished_at if finished_at is not None else row["finished_at"]
            new_artifacts = (
                json.dumps(artifacts, sort_keys=True)
                if artifacts is not None
                else row["artifacts"]
            )
            new_summary = (
                json.dumps(summary, sort_keys=True)
                if summary is not None
                else row["summary"]
            )
            new_error_code = error_code if error_code is not None else row["error_code"]
            new_error_msg = error_msg if error_msg is not None else row["error_msg"]

            cur.execute(
                """UPDATE runs SET status = ?, started_at = ?, finished_at = ?,
                                   artifacts = ?, summary = ?,
                                   error_code = ?, error_msg = ?
                       WHERE run_id = ?""",
                (
                    new_status,
                    new_started,
                    new_finished,
                    new_artifacts,
                    new_summary,
                    new_error_code,
                    new_error_msg,
                    run_id,
                ),
            )

        # Re-read to return canonical record.
        fresh = self.get_run(run_id)
        assert fresh is not None  # we just updated it
        return fresh

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return _row_to_run(row) if row else None

    def list_runs(
        self,
        *,
        ea_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[RunRecord]:
        sql = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []
        if ea_id is not None:
            sql += " AND ea_id = ?"
            params.append(ea_id)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_run(r) for r in rows]

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _row_to_ea(row: sqlite3.Row) -> EARecord:
    return EARecord(
        ea_id=row["ea_id"],
        ea_name=row["ea_name"],
        source_path=row["source_path"],
        sha256=row["sha256"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        ea_id=row["ea_id"],
        kind=row["kind"],
        status=row["status"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        symbol=row["symbol"],
        period=row["period"],
        from_date=row["from_date"],
        to_date=row["to_date"],
        artifacts=json.loads(row["artifacts"] or "{}"),
        summary=json.loads(row["summary"] or "{}"),
        error_code=row["error_code"],
        error_msg=row["error_msg"],
    )


__all__ = [
    "SCHEMA_VERSION",
    "EARecord",
    "RunRecord",
    "StateStore",
]

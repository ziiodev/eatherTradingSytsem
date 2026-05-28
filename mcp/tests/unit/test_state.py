"""Unit tests for the SQLite state store."""

from __future__ import annotations

import pytest

from mcp_metatrader5.errors import StateError
from mcp_metatrader5.state import SCHEMA_VERSION, StateStore


def test_state_initialises_schema(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        cur = store._conn.execute("SELECT version FROM schema_version")
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["version"] == SCHEMA_VERSION


def test_upsert_and_get_ea(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        rec = store.upsert_ea(
            ea_id="my-ea",
            ea_name="MyEA",
            source_path="MQL5/Experts/managed/my-ea/my-ea.mq5",
            sha256="0" * 64,
        )
        assert rec.ea_id == "my-ea"
        again = store.get_ea("my-ea")
        assert again is not None
        assert again.ea_name == "MyEA"


def test_upsert_ea_updates_existing(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        rec1 = store.upsert_ea(
            ea_id="my-ea",
            ea_name="MyEA",
            source_path="path/v1.mq5",
            sha256="a" * 64,
        )
        rec2 = store.upsert_ea(
            ea_id="my-ea",
            ea_name="MyEA",
            source_path="path/v2.mq5",
            sha256="b" * 64,
        )
        assert rec2.created_at == rec1.created_at
        assert rec2.updated_at >= rec1.updated_at
        assert rec2.source_path == "path/v2.mq5"
        assert rec2.sha256 == "b" * 64


def test_list_eas(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        store.upsert_ea(ea_id="a", ea_name="A", source_path="a.mq5", sha256="1" * 64)
        store.upsert_ea(ea_id="b", ea_name="B", source_path="b.mq5", sha256="2" * 64)
        eas = store.list_eas()
        assert {e.ea_id for e in eas} == {"a", "b"}


def test_create_and_get_run(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        store.upsert_ea(ea_id="ea", ea_name="EA", source_path="x.mq5", sha256="0" * 64)
        run = store.create_run(
            run_id="20260101T000000Z-abcdef",
            ea_id="ea",
            kind="backtest",
            symbol="XAUUSD",
            period="D1",
            from_date="2024.01.01",
            to_date="2024.06.30",
        )
        assert run.status == "queued"
        fetched = store.get_run("20260101T000000Z-abcdef")
        assert fetched is not None
        assert fetched.symbol == "XAUUSD"


def test_update_run(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        store.upsert_ea(ea_id="ea", ea_name="EA", source_path="x.mq5", sha256="0" * 64)
        store.create_run(run_id="r1", ea_id="ea", kind="compile")
        updated = store.update_run(
            "r1",
            status="done",
            artifacts={"compile_log": "compile.log"},
            summary={"errors": 0, "warnings": 1},
        )
        assert updated.status == "done"
        assert updated.artifacts == {"compile_log": "compile.log"}
        assert updated.summary == {"errors": 0, "warnings": 1}


def test_update_unknown_run_raises(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store, pytest.raises(StateError):
        store.update_run("does-not-exist", status="done")


def test_list_runs_filters(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        store.upsert_ea(ea_id="ea", ea_name="EA", source_path="x.mq5", sha256="0" * 64)
        store.create_run(run_id="r-compile", ea_id="ea", kind="compile")
        store.create_run(run_id="r-backtest", ea_id="ea", kind="backtest")
        store.create_run(run_id="r-optimize", ea_id="ea", kind="optimize")
        compiles = store.list_runs(kind="compile")
        assert [r.run_id for r in compiles] == ["r-compile"]
        all_runs = store.list_runs()
        assert {r.run_id for r in all_runs} == {"r-compile", "r-backtest", "r-optimize"}


def test_delete_ea_cascades_runs(tmp_path) -> None:
    db = tmp_path / "state.sqlite"
    with StateStore(db) as store:
        store.upsert_ea(ea_id="ea", ea_name="EA", source_path="x.mq5", sha256="0" * 64)
        store.create_run(run_id="r1", ea_id="ea", kind="compile")
        assert store.delete_ea("ea") is True
        assert store.get_run("r1") is None

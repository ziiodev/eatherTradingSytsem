"""Unit tests for the pydantic tool schemas."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from mcp_metatrader5.tools.schemas import (
    BacktestEAInput,
    BacktestEAOutput,
    CompileEAInput,
    CompileEAOutput,
    EaDetail,
    EaSummary,
    GetEAInput,
    GetEAOutput,
    GetRunArtifactInput,
    GetRunArtifactOutput,
    GetRunInput,
    GetRunOutput,
    ListEAsInput,
    ListEAsOutput,
    ListRunsInput,
    ListRunsOutput,
    OptimizeEAInput,
    OptimizeEAOutput,
    ParameterRange,
    RegisterEAInput,
    RegisterEAOutput,
    RemoveEAInput,
    RemoveEAOutput,
    RunDetail,
    RunSummary,
    Timeframe,
)

# ---------------------------------------------------------------------------
# Timeframe enum
# ---------------------------------------------------------------------------


def test_timeframe_accepts_known_values() -> None:
    for tf in ("M1", "M5", "M15", "H1", "H4", "D1", "W1", "MN1"):
        assert Timeframe(tf) == Timeframe[tf]


def test_timeframe_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        Timeframe("XYZ")


# ---------------------------------------------------------------------------
# ParameterRange
# ---------------------------------------------------------------------------


def test_parameter_range_with_start_stop_step() -> None:
    p = ParameterRange(start=0.1, stop=1.0, step=0.05)
    assert p.start == 0.1
    assert p.stop == 1.0
    assert p.step == 0.05
    assert p.values is None


def test_parameter_range_with_explicit_values() -> None:
    p = ParameterRange(values=[1, 2, 3])
    assert p.values == [1, 2, 3]
    assert p.start is None


def test_parameter_range_requires_one_form() -> None:
    with pytest.raises(ValidationError):
        ParameterRange()


def test_parameter_range_rejects_both_forms() -> None:
    with pytest.raises(ValidationError):
        ParameterRange(start=0.1, stop=1.0, step=0.05, values=[1, 2])


def test_parameter_range_step_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ParameterRange(start=0.1, stop=1.0, step=0.0)


def test_parameter_range_stop_must_exceed_start() -> None:
    with pytest.raises(ValidationError):
        ParameterRange(start=1.0, stop=0.5, step=0.1)


# ---------------------------------------------------------------------------
# RegisterEA
# ---------------------------------------------------------------------------


def test_register_ea_input_minimal(tmp_path) -> None:
    f = tmp_path / "MyEA.mq5"
    f.write_text("// stub\n")
    inp = RegisterEAInput(source_path=f)
    assert inp.source_path == f
    assert inp.ea_name is None
    assert inp.overwrite is False


def test_register_ea_input_rejects_extra_field(tmp_path) -> None:
    f = tmp_path / "MyEA.mq5"
    f.write_text("// stub\n")
    with pytest.raises(ValidationError):
        RegisterEAInput(source_path=f, bogus="x")  # type: ignore[call-arg]


def test_register_ea_output_round_trip() -> None:
    out = RegisterEAOutput(
        ea_handle="my-ea",
        workspace_path="/some/path/MyEA.mq5",
        registered_at="2026-01-01T00:00:00Z",
    )
    assert out.ea_handle == "my-ea"


# ---------------------------------------------------------------------------
# Compile / Backtest / Optimize inputs
# ---------------------------------------------------------------------------


def test_compile_ea_input() -> None:
    inp = CompileEAInput(ea_handle="my-ea")
    assert inp.ea_handle == "my-ea"


def test_compile_ea_output_status_default() -> None:
    out = CompileEAOutput(run_id="20260101t000000z-aaaaaa")
    assert out.status == "queued"


def test_backtest_and_optimize_outputs_default_status() -> None:
    assert BacktestEAOutput(run_id="x").status == "queued"
    assert OptimizeEAOutput(run_id="x").status == "queued"


def test_backtest_ea_input_full() -> None:
    inp = BacktestEAInput(
        ea_handle="my-ea",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        date_from=date(2024, 1, 1),
        date_to=date(2024, 6, 30),
        deposit=10000.0,
        currency="USD",
        leverage=100,
        model=0,
        spread=20,
        ea_inputs={"Lots": 0.1, "StopLoss": 200},
    )
    assert inp.symbol == "XAUUSD"
    assert inp.timeframe == Timeframe.H1
    assert inp.date_from == date(2024, 1, 1)


def test_backtest_ea_input_accepts_iso_string_for_date() -> None:
    inp = BacktestEAInput(
        ea_handle="my-ea",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        date_from="2024-01-01",  # type: ignore[arg-type]
        date_to="2024-06-30",  # type: ignore[arg-type]
        deposit=10000.0,
        currency="USD",
        leverage=100,
        model=0,
        spread=20,
    )
    assert inp.date_from == date(2024, 1, 1)


def test_backtest_ea_input_rejects_inverted_dates() -> None:
    with pytest.raises(ValidationError):
        BacktestEAInput(
            ea_handle="my-ea",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            date_from=date(2024, 12, 31),
            date_to=date(2024, 1, 1),
            deposit=10000.0,
            currency="USD",
            leverage=100,
            model=0,
            spread=20,
        )


def test_backtest_ea_input_currency_must_be_three_uppercase_letters() -> None:
    with pytest.raises(ValidationError):
        BacktestEAInput(
            ea_handle="my-ea",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 30),
            deposit=10000.0,
            currency="usd",
            leverage=100,
            model=0,
            spread=20,
        )


def test_backtest_ea_input_deposit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        BacktestEAInput(
            ea_handle="my-ea",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 30),
            deposit=0.0,
            currency="USD",
            leverage=100,
            model=0,
            spread=20,
        )


def test_backtest_ea_input_model_range() -> None:
    with pytest.raises(ValidationError):
        BacktestEAInput(
            ea_handle="my-ea",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 30),
            deposit=10000.0,
            currency="USD",
            leverage=100,
            model=99,
            spread=20,
        )


def test_optimize_ea_input_full() -> None:
    inp = OptimizeEAInput(
        ea_handle="my-ea",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        date_from=date(2024, 1, 1),
        date_to=date(2024, 6, 30),
        deposit=10000.0,
        currency="USD",
        leverage=100,
        model=2,
        spread=20,
        parameter_ranges={
            "Lots": ParameterRange(start=0.1, stop=1.0, step=0.05),
            "StopLoss": ParameterRange(values=[100, 200, 300]),
        },
        criterion=0,
        mode=2,
    )
    assert "Lots" in inp.parameter_ranges
    assert inp.criterion == 0


def test_optimize_ea_input_requires_at_least_one_parameter() -> None:
    with pytest.raises(ValidationError):
        OptimizeEAInput(
            ea_handle="my-ea",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 30),
            deposit=10000.0,
            currency="USD",
            leverage=100,
            model=2,
            spread=20,
            parameter_ranges={},
            criterion=0,
            mode=2,
        )


def test_optimize_ea_input_mode_disabled_rejected() -> None:
    with pytest.raises(ValidationError):
        OptimizeEAInput(
            ea_handle="my-ea",
            symbol="XAUUSD",
            timeframe=Timeframe.H1,
            date_from=date(2024, 1, 1),
            date_to=date(2024, 6, 30),
            deposit=10000.0,
            currency="USD",
            leverage=100,
            model=2,
            spread=20,
            parameter_ranges={"Lots": ParameterRange(start=0.1, stop=0.5, step=0.05)},
            criterion=0,
            mode=0,  # disabled
        )


# ---------------------------------------------------------------------------
# List/Get/Remove
# ---------------------------------------------------------------------------


def test_list_eas_input_no_args() -> None:
    inp = ListEAsInput()
    assert inp.model_dump() == {}


def test_list_eas_output_collects_summaries() -> None:
    out = ListEAsOutput(
        eas=[
            EaSummary(
                ea_handle="my-ea",
                ea_name="MyEA",
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-02T00:00:00Z",
                sha256="0" * 64,
            )
        ]
    )
    assert len(out.eas) == 1


def test_get_ea_input() -> None:
    inp = GetEAInput(ea_handle="my-ea")
    assert inp.ea_handle == "my-ea"


def test_get_ea_output_detail_optional_workspace() -> None:
    detail = EaDetail(
        ea_handle="my-ea",
        ea_name="MyEA",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z",
        sha256="0" * 64,
        workspace_path="MQL5/Experts/managed/my-ea/my-ea.mq5",
    )
    out = GetEAOutput(ea=detail)
    assert out.ea.ea_handle == "my-ea"


def test_remove_ea_input_default_keeps_workspace() -> None:
    inp = RemoveEAInput(ea_handle="my-ea")
    assert inp.also_delete_workspace is False


def test_remove_ea_output() -> None:
    out = RemoveEAOutput(removed=True)
    assert out.removed is True


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def test_list_runs_input_defaults() -> None:
    inp = ListRunsInput()
    assert inp.limit == 50
    assert inp.offset == 0
    assert inp.ea_handle is None
    assert inp.status is None


def test_list_runs_input_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        ListRunsInput(limit=0)
    with pytest.raises(ValidationError):
        ListRunsInput(limit=10_000)


def test_list_runs_output() -> None:
    out = ListRunsOutput(
        runs=[
            RunSummary(
                run_id="20260101t000000z-aaaaaa",
                ea_handle="my-ea",
                kind="compile",
                status="done",
                created_at="2026-01-01T00:00:00Z",
            )
        ]
    )
    assert len(out.runs) == 1


def test_get_run_input() -> None:
    inp = GetRunInput(run_id="20260101t000000z-aaaaaa")
    assert inp.run_id.startswith("2026")


def test_run_detail_optional_error_fields() -> None:
    rd = RunDetail(
        run_id="20260101t000000z-aaaaaa",
        ea_handle="my-ea",
        kind="backtest",
        status="failed",
        created_at="2026-01-01T00:00:00Z",
        error_kind="backtest_failed",
        error_message="terminal exited 1",
        summary={"exit_code": 1},
    )
    out = GetRunOutput(run=rd)
    assert out.run.error_kind == "backtest_failed"


def test_get_run_artifact_input_artifact_enum() -> None:
    inp = GetRunArtifactInput(run_id="20260101t000000z-aaaaaa", artifact="report")
    assert inp.artifact == "report"
    with pytest.raises(ValidationError):
        GetRunArtifactInput(run_id="x", artifact="bogus")  # type: ignore[arg-type]


def test_get_run_artifact_output_strict_text() -> None:
    out = GetRunArtifactOutput(content="hello", encoding="utf-8", mime_type="text/plain")
    assert out.encoding == "utf-8"


# ---------------------------------------------------------------------------
# Strict mode: all input models forbid extra fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    [
        CompileEAInput,
        GetEAInput,
        GetRunInput,
        ListRunsInput,
        RemoveEAInput,
    ],
)
def test_input_models_forbid_extra(model_cls) -> None:
    with pytest.raises(ValidationError):
        if model_cls is ListRunsInput:
            model_cls(extra_field="bad")
        else:
            model_cls(ea_handle="x", run_id="x", extra_field="bad")  # type: ignore[call-arg]

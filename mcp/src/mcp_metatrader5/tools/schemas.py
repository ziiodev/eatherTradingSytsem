"""Pydantic input/output schemas for the MCP tool surface.

Each MCP tool has exactly one ``*Input`` and one ``*Output`` model. Models are
strict (``extra="forbid"``) so unknown keys are rejected — both to surface
typos early and to keep the JSON-RPC schema honest.

Tool list (10):
    1. mt5_register_ea
    2. mt5_compile_ea
    3. mt5_backtest_ea
    4. mt5_optimize_ea
    5. mt5_list_eas
    6. mt5_get_ea
    7. mt5_remove_ea
    8. mt5_list_runs
    9. mt5_get_run
    10. mt5_get_run_artifact

Phase 5 will wire these to ``RunManager``; this module is **pure** validation.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Shared base / aliases
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Base for every schema in this module.

    - ``extra="forbid"`` rejects unknown fields.
    - ``frozen=True`` keeps inputs immutable once validated.
    - ``str_strip_whitespace=True`` normalises stringy inputs.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
    )


_EAHandle = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"),
]
_RunId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80),
]
_Currency = Annotated[
    str,
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]
_Sha256 = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
_Symbol = Annotated[
    str,
    StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$"),
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Timeframe(StrEnum):
    """MT5 chart period codes accepted by the tester."""

    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"
    M6 = "M6"
    M10 = "M10"
    M12 = "M12"
    M15 = "M15"
    M20 = "M20"
    M30 = "M30"
    H1 = "H1"
    H2 = "H2"
    H3 = "H3"
    H4 = "H4"
    H6 = "H6"
    H8 = "H8"
    H12 = "H12"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


class RunKind(StrEnum):
    COMPILE = "compile"
    BACKTEST = "backtest"
    OPTIMIZE = "optimize"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# ParameterRange
# ---------------------------------------------------------------------------


class ParameterRange(_StrictModel):
    """Either a numeric range (start/stop/step) **or** an explicit value list.

    Exactly one form must be supplied. Used by ``mt5_optimize_ea``.
    """

    start: Annotated[float | None, Field(default=None, description="Range start (inclusive).")] = None
    stop: Annotated[float | None, Field(default=None, description="Range stop (inclusive).")] = None
    step: Annotated[float | None, Field(default=None, description="Step size, must be > 0.")] = None
    values: Annotated[
        list[float] | None,
        Field(default=None, description="Explicit list of values; mutually exclusive with start/stop/step."),
    ] = None

    @model_validator(mode="after")
    def _validate_form(self) -> ParameterRange:
        range_form = self.start is not None or self.stop is not None or self.step is not None
        list_form = self.values is not None
        if range_form and list_form:
            raise ValueError(
                "ParameterRange must use either start/stop/step OR values, not both"
            )
        if not range_form and not list_form:
            raise ValueError(
                "ParameterRange must provide either start/stop/step or values"
            )
        if range_form:
            missing = [
                name
                for name, val in (("start", self.start), ("stop", self.stop), ("step", self.step))
                if val is None
            ]
            if missing:
                raise ValueError(
                    f"ParameterRange (range form) missing fields: {', '.join(missing)}"
                )
            assert self.start is not None and self.stop is not None and self.step is not None
            if self.step <= 0:
                raise ValueError(f"ParameterRange.step must be > 0 (got {self.step})")
            if self.stop <= self.start:
                raise ValueError(
                    f"ParameterRange.stop ({self.stop}) must exceed start ({self.start})"
                )
        if list_form:
            assert self.values is not None
            if not self.values:
                raise ValueError("ParameterRange.values must be non-empty")
        return self


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class EaSummary(_StrictModel):
    ea_handle: _EAHandle
    ea_name: Annotated[str, Field(min_length=1, max_length=128)]
    created_at: Annotated[str, Field(description="ISO-8601 UTC timestamp.")]
    updated_at: Annotated[str, Field(description="ISO-8601 UTC timestamp.")]
    sha256: _Sha256


class EaDetail(_StrictModel):
    ea_handle: _EAHandle
    ea_name: Annotated[str, Field(min_length=1, max_length=128)]
    created_at: str
    updated_at: str
    sha256: _Sha256
    workspace_path: Annotated[
        str,
        Field(description="Path to the .mq5 source inside MT5 MQL5/Experts/managed/."),
    ]


class RunSummary(_StrictModel):
    run_id: _RunId
    ea_handle: _EAHandle
    kind: RunKind
    status: RunStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class RunDetail(_StrictModel):
    run_id: _RunId
    ea_handle: _EAHandle
    kind: RunKind
    status: RunStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    log_path: str | None = None
    error_kind: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# 1. mt5_register_ea
# ---------------------------------------------------------------------------


class RegisterEAInput(_StrictModel):
    source_path: Annotated[
        Path,
        Field(description="Absolute path to a .mq5 source file readable by the server."),
    ]
    ea_name: Annotated[
        str | None,
        Field(default=None, max_length=128, description="Optional canonical name; defaults to source basename."),
    ] = None
    overwrite: Annotated[
        bool,
        Field(default=False, description="If true, replace an existing EA with the same handle."),
    ] = False


class RegisterEAOutput(_StrictModel):
    ea_handle: _EAHandle
    workspace_path: Annotated[str, Field(description="Path inside the workspace where the .mq5 lives.")]
    registered_at: Annotated[str, Field(description="ISO-8601 UTC timestamp.")]


# ---------------------------------------------------------------------------
# 2. mt5_compile_ea
# ---------------------------------------------------------------------------


class CompileEAInput(_StrictModel):
    ea_handle: _EAHandle


class CompileEAOutput(_StrictModel):
    run_id: _RunId
    status: Literal["queued"] = "queued"


# ---------------------------------------------------------------------------
# 3. mt5_backtest_ea
# ---------------------------------------------------------------------------


class _TesterBaseInput(_StrictModel):
    """Shared fields for backtest + optimize inputs."""

    ea_handle: _EAHandle
    symbol: _Symbol
    timeframe: Timeframe
    date_from: date
    date_to: date
    deposit: Annotated[float, Field(gt=0, description="Initial deposit; must be positive.")]
    currency: _Currency
    leverage: Annotated[int, Field(gt=0, le=10_000, description="Account leverage (1:N); pass N as int.")]
    model: Annotated[
        int,
        Field(ge=0, le=4, description="Tick model: 0=every-tick, 1=1m-OHLC, 2=open, 3=math, 4=real-tick."),
    ]
    spread: Annotated[int, Field(ge=0, le=10_000, description="Tester spread in points; 0 means current spread.")]

    @model_validator(mode="after")
    def _check_dates(self) -> _TesterBaseInput:
        if self.date_to < self.date_from:
            raise ValueError(
                f"date_to ({self.date_to}) must be on or after date_from ({self.date_from})"
            )
        return self


class BacktestEAInput(_TesterBaseInput):
    ea_inputs: Annotated[
        dict[str, Any],
        Field(default_factory=dict, description="EA input parameter overrides for [TesterInputs]."),
    ] = Field(default_factory=dict)

    @field_validator("ea_inputs", mode="before")
    @classmethod
    def _coerce_inputs(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value


class BacktestEAOutput(_StrictModel):
    run_id: _RunId
    status: Literal["queued"] = "queued"


# ---------------------------------------------------------------------------
# 4. mt5_optimize_ea
# ---------------------------------------------------------------------------


class OptimizeEAInput(_TesterBaseInput):
    parameter_ranges: Annotated[
        dict[str, ParameterRange],
        Field(description="Map of EA input parameter name to its sweep specification."),
    ]
    criterion: Annotated[
        int,
        Field(ge=0, le=6, description="OptimizationCriterion id (0=max profit, 1=PF, ...)."),
    ]
    mode: Annotated[
        int,
        Field(ge=1, le=4, description="Optimization mode: 1=slow-complete, 2=slow-genetic, 3=all-symbols, 4=fast-genetic."),
    ]

    @field_validator("parameter_ranges")
    @classmethod
    def _at_least_one(cls, value: dict[str, ParameterRange]) -> dict[str, ParameterRange]:
        if not value:
            raise ValueError("parameter_ranges must contain at least one parameter")
        return value


class OptimizeEAOutput(_StrictModel):
    run_id: _RunId
    status: Literal["queued"] = "queued"


# ---------------------------------------------------------------------------
# 5. mt5_list_eas
# ---------------------------------------------------------------------------


class ListEAsInput(_StrictModel):
    pass


class ListEAsOutput(_StrictModel):
    eas: list[EaSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 6. mt5_get_ea
# ---------------------------------------------------------------------------


class GetEAInput(_StrictModel):
    ea_handle: _EAHandle


class GetEAOutput(_StrictModel):
    ea: EaDetail


# ---------------------------------------------------------------------------
# 7. mt5_remove_ea
# ---------------------------------------------------------------------------


class RemoveEAInput(_StrictModel):
    ea_handle: _EAHandle
    also_delete_workspace: Annotated[
        bool,
        Field(default=False, description="If true, also delete the EA's workspace files on disk."),
    ] = False


class RemoveEAOutput(_StrictModel):
    removed: bool


# ---------------------------------------------------------------------------
# 8. mt5_list_runs
# ---------------------------------------------------------------------------


class ListRunsInput(_StrictModel):
    ea_handle: _EAHandle | None = None
    status: RunStatus | None = None
    limit: Annotated[int, Field(default=50, ge=1, le=500)] = 50
    offset: Annotated[int, Field(default=0, ge=0)] = 0


class ListRunsOutput(_StrictModel):
    runs: list[RunSummary] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. mt5_get_run
# ---------------------------------------------------------------------------


class GetRunInput(_StrictModel):
    run_id: _RunId


class GetRunOutput(_StrictModel):
    run: RunDetail


# ---------------------------------------------------------------------------
# 10. mt5_get_run_artifact
# ---------------------------------------------------------------------------


ArtifactKind = Literal["report", "log", "results"]


class GetRunArtifactInput(_StrictModel):
    run_id: _RunId
    artifact: ArtifactKind


class GetRunArtifactOutput(_StrictModel):
    content: Annotated[
        str,
        Field(description="Artifact body. Binary content is base64-encoded when encoding=='base64'."),
    ]
    encoding: Literal["utf-8", "utf-16-le", "base64"] = "utf-8"
    mime_type: Annotated[str, Field(description="Best-effort IANA MIME type.")]


__all__ = [
    "ArtifactKind",
    "BacktestEAInput",
    "BacktestEAOutput",
    "CompileEAInput",
    "CompileEAOutput",
    "EaDetail",
    "EaSummary",
    "GetEAInput",
    "GetEAOutput",
    "GetRunArtifactInput",
    "GetRunArtifactOutput",
    "GetRunInput",
    "GetRunOutput",
    "ListEAsInput",
    "ListEAsOutput",
    "ListRunsInput",
    "ListRunsOutput",
    "OptimizeEAInput",
    "OptimizeEAOutput",
    "ParameterRange",
    "RegisterEAInput",
    "RegisterEAOutput",
    "RemoveEAInput",
    "RemoveEAOutput",
    "RunDetail",
    "RunKind",
    "RunStatus",
    "RunSummary",
    "Timeframe",
]

"""Builder for MT5 ``terminal64.exe /config:<file>.ini`` Strategy-Tester INIs.

The MT5 terminal accepts an INI configuration with a ``[Tester]`` section that
controls a backtest or optimization run, and a ``[TesterInputs]`` section with
EA input parameter overrides.

This module is **pure**: it produces a string. Writing the file to disk is the
caller's responsibility (see :mod:`mcp_metatrader5.workspace`).

References (consolidated from official MT5 documentation):

- ``Expert``: path to compiled ``.ex5`` relative to ``MQL5/Experts/`` inside
  the data folder of the MT5 install.
- ``Symbol``, ``Period``, ``FromDate``, ``ToDate``: tester window.
- ``Model``: tick generation mode (0..4).
- ``Optimization``: 0=disabled, 1=slow complete, 2=slow genetic,
  3=all symbols slow, 4=fast genetic (single symbol).
- ``OptimizationCriterion``: 0..6 criterion id.
- ``Report``: relative output report filename. ``ReplaceReport=1`` overwrites.
- ``ShutdownTerminal=1``: required for headless runs to exit cleanly.

INI files for MT5 are CRLF-terminated and ASCII-encoded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum

# ----- enums ----------------------------------------------------------------


class TickModel(IntEnum):
    EVERY_TICK = 0
    ONE_MINUTE_OHLC = 1
    OPEN_PRICES = 2
    MATH_CALC = 3
    EVERY_TICK_REAL = 4


class OptimizationMode(IntEnum):
    DISABLED = 0
    SLOW_COMPLETE = 1
    SLOW_GENETIC = 2
    ALL_SYMBOLS = 3
    FAST_GENETIC = 4


class OptimizationCriterion(IntEnum):
    MAX_PROFIT = 0
    MAX_PROFIT_FACTOR = 1
    MAX_RECOVERY_FACTOR = 2
    MAX_SHARPE_RATIO = 3
    CUSTOM_MAX = 4
    COMPLEX_CRITERION = 5
    BALANCE_DRAWDOWN = 6


# ----- validation helpers ---------------------------------------------------

_VALID_PERIODS: frozenset[str] = frozenset(
    {
        "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
        "H1", "H2", "H3", "H4", "H6", "H8", "H12",
        "D1", "W1", "MN1",
    }
)
_DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def _validate_common(
    *,
    expert: str,
    symbol: str,
    period: str,
    from_date: str,
    to_date: str,
    leverage: int,
    deposit: float,
    currency: str,
) -> None:
    if not expert or not expert.endswith(".ex5"):
        raise ValueError(f"expert must be a path ending in .ex5 (got {expert!r})")
    if not symbol or not symbol.isascii():
        raise ValueError(f"symbol must be non-empty ASCII (got {symbol!r})")
    if period not in _VALID_PERIODS:
        raise ValueError(
            f"period must be one of {sorted(_VALID_PERIODS)} (got {period!r})"
        )
    for label, value in (("from_date", from_date), ("to_date", to_date)):
        if not _DATE_RE.match(value):
            raise ValueError(f"{label} must be YYYY.MM.DD (got {value!r})")
    if leverage <= 0:
        raise ValueError(f"leverage must be positive (got {leverage})")
    if deposit <= 0:
        raise ValueError(f"deposit must be positive (got {deposit})")
    if not currency or len(currency) != 3 or not currency.isupper():
        raise ValueError(f"currency must be a 3-letter uppercase ISO code (got {currency!r})")


# ----- dataclasses ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    expert: str
    symbol: str
    period: str
    from_date: str
    to_date: str
    deposit: float
    currency: str
    leverage: int
    model: TickModel = TickModel.EVERY_TICK
    report: str = "report.html"
    execution_mode: int = 0
    inputs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptimizationConfig:
    expert: str
    symbol: str
    period: str
    from_date: str
    to_date: str
    deposit: float
    currency: str
    leverage: int
    optimization_mode: OptimizationMode
    optimization_criterion: OptimizationCriterion
    parameters: dict[str, tuple[str, str, str, bool]]
    """Mapping of parameter name to ``(start, step, max, enabled)``.

    All four positional fields are strings (or bool for ``enabled``) so we
    don't lose precision on user-provided floats. The MT5 INI format is
    ``Name=start||min||step||max||Y|N``; we use the same value for ``min`` and
    ``start``.
    """

    model: TickModel = TickModel.OPEN_PRICES
    report: str = "report.xml"
    execution_mode: int = 0


# ----- rendering ------------------------------------------------------------


_CRLF = "\r\n"


def _render_section(name: str, pairs: list[tuple[str, str]]) -> str:
    lines = [f"[{name}]"]
    lines.extend(f"{k}={v}" for k, v in pairs)
    return _CRLF.join(lines) + _CRLF


def _common_pairs(
    *,
    expert: str,
    symbol: str,
    period: str,
    from_date: str,
    to_date: str,
    deposit: float,
    currency: str,
    leverage: int,
    model: TickModel,
    optimization: int,
    optimization_criterion: int | None,
    report: str,
    execution_mode: int,
) -> list[tuple[str, str]]:
    deposit_str = f"{int(deposit)}" if float(deposit).is_integer() else f"{deposit:.2f}"
    pairs: list[tuple[str, str]] = [
        ("Expert", expert),
        ("Symbol", symbol),
        ("Period", period),
        ("FromDate", from_date),
        ("ToDate", to_date),
        ("Deposit", deposit_str),
        ("Currency", currency),
        ("Leverage", f"1:{leverage}"),
        ("Model", str(int(model))),
        ("ExecutionMode", str(execution_mode)),
        ("Optimization", str(optimization)),
    ]
    if optimization_criterion is not None:
        pairs.append(("OptimizationCriterion", str(optimization_criterion)))
    pairs.extend(
        [
            ("ShutdownTerminal", "1"),
            ("Report", report),
            ("ReplaceReport", "1"),
        ]
    )
    return pairs


def build_backtest_ini(cfg: BacktestConfig) -> str:
    """Render an INI for a single backtest run."""

    _validate_common(
        expert=cfg.expert,
        symbol=cfg.symbol,
        period=cfg.period,
        from_date=cfg.from_date,
        to_date=cfg.to_date,
        leverage=cfg.leverage,
        deposit=cfg.deposit,
        currency=cfg.currency,
    )

    tester = _common_pairs(
        expert=cfg.expert,
        symbol=cfg.symbol,
        period=cfg.period,
        from_date=cfg.from_date,
        to_date=cfg.to_date,
        deposit=cfg.deposit,
        currency=cfg.currency,
        leverage=cfg.leverage,
        model=cfg.model,
        optimization=int(OptimizationMode.DISABLED),
        optimization_criterion=None,
        report=cfg.report,
        execution_mode=cfg.execution_mode,
    )
    out = _render_section("Tester", tester)

    if cfg.inputs:
        input_pairs = [(name, value) for name, value in cfg.inputs.items()]
        out += _render_section("TesterInputs", input_pairs)
    return out


def build_optimize_ini(cfg: OptimizationConfig) -> str:
    """Render an INI for a Strategy-Tester optimization run."""

    _validate_common(
        expert=cfg.expert,
        symbol=cfg.symbol,
        period=cfg.period,
        from_date=cfg.from_date,
        to_date=cfg.to_date,
        leverage=cfg.leverage,
        deposit=cfg.deposit,
        currency=cfg.currency,
    )
    if cfg.optimization_mode is OptimizationMode.DISABLED:
        raise ValueError("optimization_mode must not be DISABLED for build_optimize_ini")
    if not cfg.parameters:
        raise ValueError("optimization requires at least one parameter")

    tester = _common_pairs(
        expert=cfg.expert,
        symbol=cfg.symbol,
        period=cfg.period,
        from_date=cfg.from_date,
        to_date=cfg.to_date,
        deposit=cfg.deposit,
        currency=cfg.currency,
        leverage=cfg.leverage,
        model=cfg.model,
        optimization=int(cfg.optimization_mode),
        optimization_criterion=int(cfg.optimization_criterion),
        report=cfg.report,
        execution_mode=cfg.execution_mode,
    )
    out = _render_section("Tester", tester)

    input_pairs: list[tuple[str, str]] = []
    for name, (start, step, maximum, enabled) in cfg.parameters.items():
        flag = "Y" if enabled else "N"
        # MT5 format: start||min||step||max||Y|N. We use start as min.
        value = f"{start}||{start}||{step}||{maximum}||{flag}"
        input_pairs.append((name, value))
    out += _render_section("TesterInputs", input_pairs)
    return out


__all__ = [
    "BacktestConfig",
    "OptimizationConfig",
    "OptimizationCriterion",
    "OptimizationMode",
    "TickModel",
    "build_backtest_ini",
    "build_optimize_ini",
]

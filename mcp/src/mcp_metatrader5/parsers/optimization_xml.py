"""Parser for MT5 optimization Excel-XML (SpreadsheetML) reports.

When the Strategy Tester runs an optimization with ``Report=*.xml``, the
terminal writes an Excel-2003 SpreadsheetML document containing one row per
pass. The first row is the header (column names); subsequent rows hold
numeric/string cells in the same column order.

We treat known column names (``Profit``, ``Profit Factor``, …) as first-class
metric fields and any remaining columns as **parameter values** captured in
:attr:`OptimizationPass.parameters`.

This module is **pure**: filesystem read + XML parse, no Wine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..errors import ErrorCode, MT5MCPError

_NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}


@dataclass(frozen=True, slots=True)
class OptimizationPass:
    pass_index: int
    profit: float | None = None
    expected_payoff: float | None = None
    profit_factor: float | None = None
    recovery_factor: float | None = None
    sharpe_ratio: float | None = None
    custom: float | None = None
    equity_drawdown_percent: float | None = None
    trades: int | None = None
    result: float | None = None
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OptimizationReport:
    columns: list[str]
    passes: list[OptimizationPass]

    @property
    def pass_count(self) -> int:
        return len(self.passes)

    def best_pass(self, *, by: str = "profit") -> OptimizationPass | None:
        """Return the single best pass under ``by`` (descending).

        ``by`` matches a numeric attribute on :class:`OptimizationPass`. Passes
        whose value is ``None`` are excluded from the comparison.
        """

        candidates = [p for p in self.passes if getattr(p, by, None) is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda p: getattr(p, by))


# Mapping from XML header name (lower-cased, stripped) → field name.
_METRIC_COLUMNS: dict[str, str] = {
    "pass": "pass_index",
    "result": "result",
    "profit": "profit",
    "expected payoff": "expected_payoff",
    "profit factor": "profit_factor",
    "recovery factor": "recovery_factor",
    "sharpe ratio": "sharpe_ratio",
    "custom": "custom",
    "equity dd %": "equity_drawdown_percent",
    "equity drawdown %": "equity_drawdown_percent",
    "trades": "trades",
}


def _to_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _row_cells(row: etree._Element) -> list[str]:
    cells: list[str] = []
    for cell in row.findall("ss:Cell", _NS):
        data = cell.find("ss:Data", _NS)
        cells.append((data.text or "").strip() if data is not None else "")
    return cells


def parse_optimization_xml(path: Path) -> OptimizationReport:
    """Parse an MT5 SpreadsheetML optimization report.

    Raises
    ------
    MT5MCPError
        If the file is missing, unparseable, or has no header row.
    """

    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"optimization XML not found: {path}",
        ) from exc
    except OSError as exc:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"cannot read optimization XML {path}: {exc}",
        ) from exc

    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError as exc:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"unparseable optimization XML: {path}",
        ) from exc

    rows = root.findall(".//ss:Worksheet/ss:Table/ss:Row", _NS)
    if not rows:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"optimization XML has no rows: {path}",
        )

    header = _row_cells(rows[0])
    if not header:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"optimization XML has empty header row: {path}",
        )

    passes: list[OptimizationPass] = []
    for raw_row in rows[1:]:
        cells = _row_cells(raw_row)
        if not any(cells):
            continue

        metric_kwargs: dict[str, float | int | None] = {}
        parameters: dict[str, str] = {}

        # Index alignment with header; pad/truncate as needed.
        for col_name, value in zip(header, cells, strict=False):
            key = col_name.strip()
            normalised = key.lower()
            if normalised in _METRIC_COLUMNS:
                target = _METRIC_COLUMNS[normalised]
                if target in {"trades", "pass_index"}:
                    metric_kwargs[target] = _to_int(value)
                else:
                    metric_kwargs[target] = _to_float(value)
            elif key:
                # Unknown column - treated as a parameter.
                parameters[key] = value

        raw_pass = metric_kwargs.pop("pass_index", None)
        pass_index = int(raw_pass) if raw_pass is not None else len(passes) + 1

        trades_value = metric_kwargs.get("trades")
        passes.append(
            OptimizationPass(
                pass_index=pass_index,
                profit=metric_kwargs.get("profit"),
                expected_payoff=metric_kwargs.get("expected_payoff"),
                profit_factor=metric_kwargs.get("profit_factor"),
                recovery_factor=metric_kwargs.get("recovery_factor"),
                sharpe_ratio=metric_kwargs.get("sharpe_ratio"),
                custom=metric_kwargs.get("custom"),
                equity_drawdown_percent=metric_kwargs.get("equity_drawdown_percent"),
                trades=int(trades_value) if trades_value is not None else None,
                result=metric_kwargs.get("result"),
                parameters=parameters,
            )
        )

    return OptimizationReport(columns=header, passes=passes)


__all__ = [
    "OptimizationPass",
    "OptimizationReport",
    "parse_optimization_xml",
]

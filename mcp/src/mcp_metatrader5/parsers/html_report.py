"""Parser for the MT5 Strategy-Tester HTML report.

The terminal writes ``<report>.html`` next to ``<report>.htm`` after a
backtest. The DOM is simple and stable: nested ``<table>`` blocks with two
columns per ``<tr>`` (label, value). Labels end with ``:`` and may include
units. Values may be plain strings or composite (e.g. ``138 (58.23%)``).

We rely on :mod:`lxml.html` (``HTMLParser``) for a permissive parse — many
rows have malformed attributes / unescaped entities in real reports.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lxml import html
from lxml.etree import XMLSyntaxError

from ..errors import ErrorCode, MT5MCPError

# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BacktestReport:
    expert: str | None = None
    symbol: str | None = None
    period: str | None = None
    from_date: str | None = None
    to_date: str | None = None
    initial_deposit: float | None = None
    leverage: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)

    total_net_profit: float | None = None
    gross_profit: float | None = None
    gross_loss: float | None = None
    profit_factor: float | None = None
    expected_payoff: float | None = None
    recovery_factor: float | None = None
    sharpe_ratio: float | None = None

    total_trades: int | None = None
    profit_trades: int | None = None
    loss_trades: int | None = None

    max_drawdown_money: float | None = None
    max_drawdown_percent: float | None = None

    extras: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------


_PERIOD_RE = re.compile(
    r"^(?P<period>[A-Z0-9]+)\s*\(\s*"
    r"(?P<from>\d{4}\.\d{2}\.\d{2})\s*-\s*(?P<to>\d{4}\.\d{2}\.\d{2})\s*\)\s*$"
)
_DEPOSIT_RE = re.compile(r"^(?P<amt>-?\d+(?:\.\d+)?)\s+(?P<ccy>[A-Z]{3})\s*$")
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_TWO_NUMBERS_RE = re.compile(
    r"^(?P<a>-?\d+(?:\.\d+)?)\s*\(\s*(?P<b>-?\d+(?:\.\d+)?)%?\s*\)$"
)


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _parse_two_numbers(text: str) -> tuple[float, float] | None:
    m = _TWO_NUMBERS_RE.match(text.strip())
    if not m:
        return None
    return float(m.group("a")), float(m.group("b"))


def _parse_inputs_blob(text: str) -> dict[str, str]:
    """Split ``Lots=0.10; StopLoss=200`` (or comma-separated) into a dict."""

    out: dict[str, str] = {}
    if not text.strip():
        return out
    parts = re.split(r"[;\n]+", text)
    for raw in parts:
        chunk = raw.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        out[key.strip()] = value.strip()
    return out


# ---------------------------------------------------------------------------


def _extract_rows(tree: html.HtmlElement) -> list[tuple[str, str]]:
    """Collect every two-column ``<tr>`` as (label, value) pairs.

    Labels are lower-cased, with trailing ``:`` and surrounding whitespace
    stripped. Empty/header rows are skipped.
    """

    rows: list[tuple[str, str]] = []
    for tr in tree.iter("tr"):
        cells = [c for c in tr.iter() if c.tag in ("td", "th")]
        if len(cells) != 2:
            continue
        label_raw = (cells[0].text_content() or "").strip()
        value_raw = (cells[1].text_content() or "").strip()
        if not label_raw:
            continue
        label = label_raw.rstrip(":").strip().lower()
        rows.append((label, value_raw))
    return rows


# Mapping from normalised report label → BacktestReport field.
_FIELD_MAP: dict[str, str] = {
    "expert": "expert",
    "symbol": "symbol",
    "leverage": "leverage",
    "total net profit": "total_net_profit",
    "gross profit": "gross_profit",
    "gross loss": "gross_loss",
    "profit factor": "profit_factor",
    "expected payoff": "expected_payoff",
    "recovery factor": "recovery_factor",
    "sharpe ratio": "sharpe_ratio",
    "total trades": "total_trades",
}


def parse_backtest_report(path: Path) -> BacktestReport:
    """Parse an MT5 Strategy-Tester HTML report into a :class:`BacktestReport`."""

    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"report file not found: {path}",
        ) from exc
    except OSError as exc:
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"cannot read report file {path}: {exc}",
        ) from exc

    if not raw.strip():
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"empty HTML report: {path}",
        )
    try:
        tree = html.fromstring(raw)
    except (XMLSyntaxError, ValueError) as exc:  # pragma: no cover - lxml usually recovers
        raise MT5MCPError(
            ErrorCode.REPORT_PARSE_FAILED,
            f"unparseable HTML report: {path}",
        ) from exc

    rows = _extract_rows(tree)

    fields: dict[str, Any] = {}
    extras: dict[str, str] = {}
    inputs: dict[str, str] = {}

    for label, value in rows:
        if label == "period":
            m = _PERIOD_RE.match(value)
            if m:
                fields["period"] = m.group("period")
                fields["from_date"] = m.group("from")
                fields["to_date"] = m.group("to")
            else:
                fields["period"] = value
            continue

        if label == "inputs":
            inputs = _parse_inputs_blob(value)
            continue

        if label == "initial deposit":
            m = _DEPOSIT_RE.match(value)
            if m:
                fields["initial_deposit"] = float(m.group("amt"))
            elif _NUMBER_RE.match(value):
                fields["initial_deposit"] = float(value)
            else:
                extras[label] = value
            continue

        if label.startswith("profit trades"):
            t = _parse_two_numbers(value)
            if t:
                fields["profit_trades"] = int(t[0])
            else:
                extras[label] = value
            continue

        if label.startswith("loss trades"):
            t = _parse_two_numbers(value)
            if t:
                fields["loss_trades"] = int(t[0])
            else:
                extras[label] = value
            continue

        if label == "maximal drawdown":
            t = _parse_two_numbers(value)
            if t:
                fields["max_drawdown_money"] = t[0]
                fields["max_drawdown_percent"] = t[1]
            else:
                extras[label] = value
            continue

        if label in _FIELD_MAP:
            target = _FIELD_MAP[label]
            if target == "total_trades":
                if _NUMBER_RE.match(value):
                    fields[target] = int(float(value))
                else:
                    extras[label] = value
            elif target in {
                "total_net_profit",
                "gross_profit",
                "gross_loss",
                "profit_factor",
                "expected_payoff",
                "recovery_factor",
                "sharpe_ratio",
            }:
                f = _to_float(value)
                if f is not None:
                    fields[target] = f
                else:
                    extras[label] = value
            else:
                fields[target] = value
            continue

        # everything else → extras
        extras[label] = value

    return BacktestReport(inputs=inputs, extras=extras, **fields)


__all__ = ["BacktestReport", "parse_backtest_report"]

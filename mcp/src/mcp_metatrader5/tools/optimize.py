"""Optimize-EA tool: build optimization INI and submit a job.

Translation rule for ``ParameterRange.values`` (v1)
---------------------------------------------------
MT5's optimizer only understands ``start || min || step || max || flag`` rows;
it cannot consume an arbitrary explicit-values list. So when the caller
supplies a ``values`` list:

- If the list is uniformly spaced (constant delta within a small float
  epsilon), we translate it into a single ``start/step/stop`` row.
- Otherwise we reject with ``INVALID_INPUT`` and a message asking the caller
  to provide ``start``/``stop``/``step`` directly or a uniformly-spaced list.

A single-element list is also accepted: ``start = stop = value`` and
``step = 1`` (any positive value works since min == max).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..builders.ini import (
    OptimizationConfig,
    OptimizationCriterion,
    OptimizationMode,
    TickModel,
    build_optimize_ini,
)
from ..errors import ErrorCode, MT5MCPError, WorkspaceError
from ..manager import RunManager
from ..state import StateStore
from .schemas import OptimizeEAInput, OptimizeEAOutput, ParameterRange

_FLOAT_EPS = 1e-9


def _format_date(d: object) -> str:
    return f"{d.year:04d}.{d.month:02d}.{d.day:02d}"  # type: ignore[attr-defined]


def _format_number(value: float) -> str:
    """Format a numeric value preserving integer-ness when applicable."""
    if float(value).is_integer():
        return str(int(value))
    return repr(value)


def _values_to_range(name: str, values: Sequence[float]) -> tuple[str, str, str]:
    """Translate a uniformly-spaced ``values`` list into ``(start, step, max)``.

    Raises :class:`MT5MCPError` with ``INVALID_INPUT`` if non-uniform.
    """

    if len(values) == 1:
        s = _format_number(values[0])
        return (s, "1", s)

    sorted_vals = sorted(values)
    deltas = [sorted_vals[i + 1] - sorted_vals[i] for i in range(len(sorted_vals) - 1)]
    base = deltas[0]
    if base <= 0:
        raise MT5MCPError(
            ErrorCode.INVALID_INPUT,
            f"parameter {name!r}: values list must be strictly increasing or "
            "contain unique values",
            details={"parameter": name, "values": list(values)},
        )
    if any(abs(d - base) > _FLOAT_EPS * max(1.0, abs(base)) for d in deltas):
        raise MT5MCPError(
            ErrorCode.INVALID_INPUT,
            f"parameter {name!r}: MT5 optimizer requires uniform stepping; "
            "provide start/stop/step or a uniformly-spaced values list. "
            f"Got non-uniform deltas: {deltas}",
            details={"parameter": name, "values": list(values), "deltas": deltas},
        )
    return (
        _format_number(sorted_vals[0]),
        _format_number(base),
        _format_number(sorted_vals[-1]),
    )


def _range_to_triplet(name: str, pr: ParameterRange) -> tuple[str, str, str, bool]:
    """Convert a :class:`ParameterRange` into the MT5 ``(start, step, max, enabled)`` tuple."""

    if pr.values is not None:
        start, step, maximum = _values_to_range(name, pr.values)
        return (start, step, maximum, True)

    assert pr.start is not None and pr.stop is not None and pr.step is not None
    return (
        _format_number(pr.start),
        _format_number(pr.step),
        _format_number(pr.stop),
        True,
    )


async def optimize_ea(
    payload: OptimizeEAInput,
    *,
    state: StateStore,
    manager: RunManager,
) -> OptimizeEAOutput:
    """Submit an optimization job; return the new ``run_id``."""

    if state.get_ea(payload.ea_handle) is None:
        raise WorkspaceError(
            ErrorCode.EA_NOT_FOUND,
            f"ea_handle {payload.ea_handle!r} not found",
            details={"ea_handle": payload.ea_handle},
        )

    from_date = _format_date(payload.date_from)
    to_date = _format_date(payload.date_to)

    parameters: dict[str, tuple[str, str, str, bool]] = {}
    for name, pr in payload.parameter_ranges.items():
        parameters[name] = _range_to_triplet(name, pr)

    cfg = OptimizationConfig(
        expert=f"managed/{payload.ea_handle}/{payload.ea_handle}.ex5",
        symbol=payload.symbol,
        period=payload.timeframe.value,
        from_date=from_date,
        to_date=to_date,
        deposit=payload.deposit,
        currency=payload.currency,
        leverage=payload.leverage,
        optimization_mode=OptimizationMode(payload.mode),
        optimization_criterion=OptimizationCriterion(payload.criterion),
        parameters=parameters,
        model=TickModel(payload.model),
    )
    ini_text = build_optimize_ini(cfg)

    run_id = await manager.submit_optimize(
        ea_handle=payload.ea_handle,
        ini_text=ini_text,
        symbol=payload.symbol,
        period=payload.timeframe.value,
        from_date=from_date,
        to_date=to_date,
    )
    return OptimizeEAOutput(run_id=run_id)


__all__ = ["optimize_ea"]

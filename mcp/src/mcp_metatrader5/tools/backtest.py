"""Backtest-EA tool: build the INI and submit a backtest job.

The tool function does the pure work (look up the EA, build the
:class:`BacktestConfig`, render the INI text) and hands off to the manager,
which queues a job for the configured ``WineRunnerProtocol``. Reading results
is the responsibility of ``mt5_get_run`` / ``mt5_get_run_artifact`` once the
job finishes.
"""

from __future__ import annotations

from ..builders.ini import BacktestConfig, TickModel, build_backtest_ini
from ..errors import ErrorCode, WorkspaceError
from ..manager import RunManager
from ..state import StateStore
from ._inputs import coerce_inputs
from .schemas import BacktestEAInput, BacktestEAOutput


def _format_date(d: object) -> str:
    """Format a ``datetime.date`` as ``YYYY.MM.DD`` (MT5's required form)."""
    return f"{d.year:04d}.{d.month:02d}.{d.day:02d}"  # type: ignore[attr-defined]


async def backtest_ea(
    payload: BacktestEAInput,
    *,
    state: StateStore,
    manager: RunManager,
) -> BacktestEAOutput:
    """Submit a backtest job; return the new ``run_id``."""

    if state.get_ea(payload.ea_handle) is None:
        raise WorkspaceError(
            ErrorCode.EA_NOT_FOUND,
            f"ea_handle {payload.ea_handle!r} not found",
            details={"ea_handle": payload.ea_handle},
        )

    from_date = _format_date(payload.date_from)
    to_date = _format_date(payload.date_to)

    cfg = BacktestConfig(
        expert=f"managed/{payload.ea_handle}/{payload.ea_handle}.ex5",
        symbol=payload.symbol,
        period=payload.timeframe.value,
        from_date=from_date,
        to_date=to_date,
        deposit=payload.deposit,
        currency=payload.currency,
        leverage=payload.leverage,
        model=TickModel(payload.model),
        inputs=coerce_inputs(dict(payload.ea_inputs)),
    )
    ini_text = build_backtest_ini(cfg)

    run_id = await manager.submit_backtest(
        ea_handle=payload.ea_handle,
        ini_text=ini_text,
        symbol=payload.symbol,
        period=payload.timeframe.value,
        from_date=from_date,
        to_date=to_date,
    )
    return BacktestEAOutput(run_id=run_id)


__all__ = ["backtest_ea"]

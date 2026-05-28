"""Compile-EA tool: enqueue a compile job through :class:`RunManager`.

Pure orchestration — actual MetaEditor invocation is delegated to whatever
``WineRunnerProtocol`` implementation the server was wired up with at startup.
The tool function returns the new ``run_id`` immediately (status=``queued``).
"""

from __future__ import annotations

from ..errors import ErrorCode, WorkspaceError
from ..manager import RunManager
from ..state import StateStore
from .schemas import CompileEAInput, CompileEAOutput


async def compile_ea(
    payload: CompileEAInput,
    *,
    state: StateStore,
    manager: RunManager,
) -> CompileEAOutput:
    """Submit a compile job for ``ea_handle`` and return the new ``run_id``."""

    if state.get_ea(payload.ea_handle) is None:
        raise WorkspaceError(
            ErrorCode.EA_NOT_FOUND,
            f"ea_handle {payload.ea_handle!r} not found",
            details={"ea_handle": payload.ea_handle},
        )
    run_id = await manager.submit_compile(ea_handle=payload.ea_handle)
    return CompileEAOutput(run_id=run_id)


__all__ = ["compile_ea"]

"""Unit tests for the error hierarchy."""

from __future__ import annotations

from mcp_metatrader5.errors import (
    BacktestError,
    CompileError,
    ConfigError,
    ErrorCode,
    MT5MCPError,
    WorkspaceError,
)


def test_error_codes_are_unique() -> None:
    values = [code.value for code in ErrorCode]
    assert len(values) == len(set(values))


def test_error_carries_code_and_message() -> None:
    err = ConfigError(ErrorCode.CONFIG_MISSING, "boom", details={"k": 1})
    assert isinstance(err, MT5MCPError)
    assert err.code is ErrorCode.CONFIG_MISSING
    assert err.message == "boom"
    assert err.details == {"k": 1}


def test_to_dict_serialises_for_mcp() -> None:
    err = WorkspaceError(ErrorCode.EA_NOT_FOUND, "missing")
    payload = err.to_dict()
    assert payload == {
        "code": "ea_not_found",
        "message": "missing",
        "details": {},
    }


def test_subclass_hierarchy() -> None:
    assert issubclass(BacktestError, MT5MCPError)
    assert issubclass(CompileError, MT5MCPError)
    assert issubclass(WorkspaceError, MT5MCPError)

"""Unit tests for the MetaEditor UTF-16 compile-log parser."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mcp_metatrader5.parsers.compile_log import (
    CompileDiagnostic,
    DiagnosticSeverity,
    parse_compile_log,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_compile_log_with_errors() -> None:
    result = parse_compile_log(_FIXTURES / "compile_with_errors.log")
    assert result.error_count == 1
    assert result.warning_count == 1
    severities = sorted({d.severity for d in result.diagnostics})
    assert DiagnosticSeverity.ERROR in severities
    assert DiagnosticSeverity.WARNING in severities

    err = next(d for d in result.diagnostics if d.severity is DiagnosticSeverity.ERROR)
    assert err.code == 188
    assert err.line == 12
    assert err.column == 5
    assert "undeclared identifier" in err.message
    assert err.file.endswith("Helpers.mqh")


def test_parse_compile_log_clean() -> None:
    result = parse_compile_log(_FIXTURES / "compile_clean.log")
    assert result.error_count == 0
    assert result.warning_count == 0
    assert result.diagnostics == []
    assert result.ok is True


def test_parse_compile_log_diag_dataclass_is_immutable() -> None:
    diag = CompileDiagnostic(
        severity=DiagnosticSeverity.ERROR,
        code=1,
        file="x.mq5",
        line=1,
        column=1,
        message="m",
    )
    with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
        diag.severity = DiagnosticSeverity.WARNING  # type: ignore[misc]


def test_parse_compile_log_handles_missing_file(tmp_path: Path) -> None:
    from mcp_metatrader5.errors import CompileError

    missing = tmp_path / "nope.log"
    with pytest.raises(CompileError):
        parse_compile_log(missing)


def test_parse_compile_log_accepts_utf8_fallback(tmp_path: Path) -> None:
    """If the log lacks a UTF-16 BOM, the parser falls back to UTF-8 with replacement."""

    text = (
        "MetaEditor build 4885\n"
        "MyEA.mq5\tcompiling\n"
        "\tMyEA.mq5(10,2) : error 42: bad thing\n"
        "\tResult: 1 error(s), 0 warning(s).\n"
    )
    p = tmp_path / "log_utf8.log"
    p.write_bytes(text.encode("utf-8"))

    result = parse_compile_log(p)
    assert result.error_count == 1
    assert result.diagnostics[0].code == 42

"""Parser for MetaEditor compile logs (UTF-16-LE BOM-prefixed).

MetaEditor 5 emits its compile log via ``/log:<file>.log``. The file is
UTF-16-LE with a BOM. Lines we care about have the form::

    \tFile.mq5(LINE,COL) : error CODE: message
    \tFile.mq5(LINE,COL) : warning CODE: message
    \tFile.mq5(LINE,COL) : information CODE: message
    \tResult: N error(s), M warning(s).

This module is **pure**: it reads bytes from disk and returns a structured
:class:`CompileResult`. Any IO failure raises :class:`CompileError` with a
stable :class:`ErrorCode`.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..errors import CompileError, ErrorCode


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "information"


@dataclass(frozen=True, slots=True)
class CompileDiagnostic:
    severity: DiagnosticSeverity
    code: int
    file: str
    line: int
    column: int
    message: str


@dataclass(frozen=True, slots=True)
class CompileResult:
    error_count: int
    warning_count: int
    diagnostics: list[CompileDiagnostic] = field(default_factory=list)
    raw_text: str = ""

    @property
    def ok(self) -> bool:
        return self.error_count == 0


# ``\tFile.mq5(LINE,COL) : <severity> CODE: <message>``
# The leading whitespace is variable; we strip then match.
_DIAG_RE = re.compile(
    r"^(?P<file>[^()]+?)\((?P<line>\d+),(?P<col>\d+)\)\s*:\s*"
    r"(?P<sev>error|warning|information)\s+(?P<code>\d+)\s*:\s*(?P<msg>.*)$"
)
_RESULT_RE = re.compile(
    r"Result:\s*(?P<errors>\d+)\s*error\(s\),\s*(?P<warnings>\d+)\s*warning\(s\)",
    re.IGNORECASE,
)


def _decode_log_bytes(raw: bytes) -> str:
    """Decode MetaEditor log bytes, preferring UTF-16-LE with BOM."""

    if raw.startswith(codecs.BOM_UTF16_LE):
        return raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le", errors="replace")
    if raw.startswith(codecs.BOM_UTF16_BE):
        return raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be", errors="replace")
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    # Heuristic: many NULs in even/odd positions → likely UTF-16 without BOM.
    if len(raw) >= 2 and raw[1] == 0 and raw[0] != 0:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_compile_log(path: Path) -> CompileResult:
    """Parse a MetaEditor compile log file.

    Parameters
    ----------
    path:
        Filesystem path to the compile log emitted by ``metaeditor64.exe /log:``.

    Raises
    ------
    CompileError
        If the file cannot be read.
    """

    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise CompileError(
            ErrorCode.COMPILE_LOG_UNREADABLE,
            f"compile log not found: {path}",
        ) from exc
    except OSError as exc:
        raise CompileError(
            ErrorCode.COMPILE_LOG_UNREADABLE,
            f"cannot read compile log {path}: {exc}",
        ) from exc

    text = _decode_log_bytes(raw)

    diagnostics: list[CompileDiagnostic] = []
    error_count = 0
    warning_count = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _DIAG_RE.match(line)
        if m:
            severity = DiagnosticSeverity(m.group("sev"))
            diagnostics.append(
                CompileDiagnostic(
                    severity=severity,
                    code=int(m.group("code")),
                    file=m.group("file").strip(),
                    line=int(m.group("line")),
                    column=int(m.group("col")),
                    message=m.group("msg").strip(),
                )
            )
            continue
        rm = _RESULT_RE.search(line)
        if rm:
            error_count = int(rm.group("errors"))
            warning_count = int(rm.group("warnings"))

    # If no Result line was seen, fall back to counting diagnostics.
    if error_count == 0 and warning_count == 0 and diagnostics:
        error_count = sum(1 for d in diagnostics if d.severity is DiagnosticSeverity.ERROR)
        warning_count = sum(1 for d in diagnostics if d.severity is DiagnosticSeverity.WARNING)

    return CompileResult(
        error_count=error_count,
        warning_count=warning_count,
        diagnostics=diagnostics,
        raw_text=text,
    )


__all__ = [
    "CompileDiagnostic",
    "CompileResult",
    "DiagnosticSeverity",
    "parse_compile_log",
]

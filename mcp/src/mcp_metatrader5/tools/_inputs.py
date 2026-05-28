"""Helpers for coercing Python values into MT5 ``[TesterInputs]`` strings.

MT5 accepts string-only INI values. The tool layer receives ``ea_inputs`` as
``dict[str, Any]`` (per-spec) and must render each value into the form MT5
parses correctly:

- ``True``/``False`` → ``"true"``/``"false"`` (MT5 expects lowercase)
- ``int``/``float`` → ``repr``-like (``str(value)``); we trust pydantic's
  earlier round-tripping rather than re-formatting floats
- ``str`` → passed through (already ASCII per pydantic constraints elsewhere)
- ``None`` → empty string (MT5 treats absence as default)
- everything else → ``str(value)``
"""

from __future__ import annotations

from typing import Any


def coerce_input_value(value: Any) -> str:
    """Render *value* as a string suitable for an MT5 INI ``Name=value`` line."""

    if isinstance(value, bool):  # bool is a subclass of int — check first
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return str(value)


def coerce_inputs(values: dict[str, Any]) -> dict[str, str]:
    """Apply :func:`coerce_input_value` to every entry in *values*."""

    return {name: coerce_input_value(v) for name, v in values.items()}


__all__ = ["coerce_input_value", "coerce_inputs"]

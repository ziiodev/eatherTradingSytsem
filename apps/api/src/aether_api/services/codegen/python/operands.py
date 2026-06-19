"""Translate a Condition operand STRING into a Python read for the list world.

A Condition operand is a free-form string from the editor. The shapes that appear
in shipping graphs:

* a LITERAL / custom expression (``30``, ``1.2345``, ``a``) -> passed through
  verbatim (already valid Python),
* a PLAIN indicator ref ``<prefix>_<id>[k]`` (``rsi_2[0]``, ``sma_A[1]``,
  ``stochk_X[0]``, ``zmean_Z[3]``) -> ``_at(ind["<prefix>_<id>"], i, k)``,
* a NAMED ZScore ref ``zscore_<id>:<output>[shift]`` -> resolved by output:
  the scalar ``value``/``zabs`` and the ``zgt``/``zlt`` signals read the computed
  ``z_<id>`` series; ``zmean``/``zstd``/``zsma`` read their own array series.

The bar shift ``k`` is applied through the skeleton ``_at(series, i, shift)``
helper, which is the single bar-index bridge (MQL5 ``var[s]`` == ``series[i-s]``).
"""

from __future__ import annotations

import re

from aether_api.services.codegen.buffer_ref import ZSCORE_ARRAY_OUTPUTS
from aether_api.services.codegen.graph import resolve_node_type
from aether_api.services.codegen.types import Node

# Plain indicator ref: ``<prefix>_<id>[<shift>]`` with NO ``:output`` token.
_PLAIN_RE = re.compile(r"^(?P<var>[A-Za-z][A-Za-z0-9]*_[^\[:\]]+)\[(?P<shift>\d+)\]$")
# Named output ref: ``<prefix>_<id>:<output>[<shift>]``.
_NAMED_RE = re.compile(
    r"^(?P<prefix>[A-Za-z]+)_(?P<id>[^\[:\]]+):(?P<output>[A-Za-z0-9_]+)"
    r"\[(?P<shift>\d+)\]$"
)


def _read(var: str, shift: int) -> str:
    """Return the Python list read ``_at(ind["<var>"], i, <shift>)``."""
    return f'_at(ind["{var}"], i, {shift})'


def translate_operand(operand: str, nodes_by_id: dict[str, Node]) -> str:
    """Return the Python expression for ``operand``.

    Plain refs and named ZScore refs become ``_at`` reads; everything else (a
    literal number, a custom token) is returned UNCHANGED so it interpolates as a
    valid Python sub-expression.
    """
    named = _NAMED_RE.match(operand)
    if named is not None:
        resolved = _translate_named(named, nodes_by_id)
        if resolved is not None:
            return resolved
    plain = _PLAIN_RE.match(operand)
    if plain is not None:
        return _read(plain.group("var"), int(plain.group("shift")))
    return operand


def _translate_named(
    match: re.Match[str], nodes_by_id: dict[str, Node]
) -> str | None:
    """Resolve a ``<prefix>_<id>:<output>[shift]`` ref, or None to pass through.

    Only ZScore named outputs are recognized (the sole multi-output indicator in
    shipping graphs). An unknown id / non-ZScore source / unknown output id
    returns None so the caller falls through to the plain/literal path.
    """
    node = nodes_by_id.get(match.group("id"))
    if node is None or resolve_node_type(node).lower() != "zscore":
        return None
    nid = match.group("id")
    output = match.group("output")
    shift = int(match.group("shift"))
    if output in ("value", "zabs"):
        z = _read(f"z_{nid}", shift)
        return f"abs({z})" if output == "zabs" else z
    if output == "zgt":
        return f"(({_read(f'z_{nid}', shift)} or 0.0) > 0)"
    if output == "zlt":
        return f"(({_read(f'z_{nid}', shift)} or 0.0) < 0)"
    if output in ZSCORE_ARRAY_OUTPUTS:
        return _read(f"{output}_{nid}", shift)
    return None

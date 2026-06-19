"""Resolve an indicator node to the MQL5 buffer variable(s) it emits.

A crossing node reads the OUTPUT of an upstream indicator (SMA/RSI/MACD) by the
exact local variable name that indicator's codegen module declares. This tiny
resolver centralizes that mapping so the crossing modules and the copy-depth
pre-render scan agree on the same names — they MUST stay byte-identical to the
``var`` produced in ``nodes/{sma,rsi,macd}.py``.

It also exposes :func:`resolve_output`, the FORWARD-LOOKING multi-output resolver
that a future multi-output indicator (or a test fixture) can target to emit a
named, shifted output. It is DORMANT for every shipping graph: the primary
output at shift 0 reproduces today's ``<prefix>_<id>[0]`` string exactly.
"""

from __future__ import annotations

from aether_api.services.codegen.helpers import node_id
from aether_api.services.codegen.types import Node

# Domain type -> buffer-var prefix, matching each indicator module's ``var``.
_BUFFER_PREFIX: dict[str, str] = {
    "sma": "sma_",
    "rsi": "rsi_",
    "macd": "macd_",
}

# Per-type declared outputs, ordered with the PRIMARY output first. SMA/RSI/MACD
# each expose a single ``value`` output. Stochastic is intentionally ABSENT: its
# two raw buffers (stochk_/stochd_) are not routed through this resolver, so
# ``resolve_output`` returns None for it and its codegen stays raw. A future
# multi-output indicator declares its outputs here (primary first).
_OUTPUTS: dict[str, tuple[str, ...]] = {
    "sma": ("value",),
    "rsi": ("value", "prev"),
    "macd": ("value",),
    # ZScore is multi-output but does NOT use the ``<prefix>_<id>[shift]`` array
    # form for its primary/scalar/signal outputs — it resolves through a
    # dedicated EXPRESSION branch in ``resolve_output`` (see below). Its array
    # outputs (zmean/zstd/zsma) DO read ``z<name>_<id>[shift]``. Listed here so
    # the operand translator and topology validators recognize every output id.
    "zscore": ("value", "zabs", "zmean", "zstd", "zsma", "zgt", "zlt"),
}

# ZScore output ids that resolve to an ARRAY read (``z<name>_<id>[shift]``).
# These are the only ZScore outputs a crossing value-edge may consume.
ZSCORE_ARRAY_OUTPUTS: frozenset[str] = frozenset({"zmean", "zstd", "zsma"})
# ZScore SIGNAL output ids (boolean exprs) — only valid into a combinator.
ZSCORE_SIGNAL_OUTPUTS: frozenset[str] = frozenset({"zgt", "zlt"})
# Per-array-output buffer-var prefix (matches ``nodes/zscore.py`` var names).
_ZSCORE_ARRAY_PREFIX: dict[str, str] = {
    "zmean": "zmean_",
    "zstd": "zstd_",
    "zsma": "zsma_",
}


def _domain_type(node: Node) -> str:
    """Resolve a node's domain type from ``type`` or nested ``data.type``.

    Mirrors ``generator.resolve_node_type`` but kept local to avoid a circular
    import (generator imports this module for the copy-depth scan).
    """
    top = str(node.get("type") or "")
    if top and top.lower() != "custom":
        return top
    data = node.get("data") or {}
    return str(data.get("type", ""))


def buffer_var(node: Node) -> str | None:
    """Return the bare buffer var name for an SMA/RSI/MACD node, else ``None``.

    e.g. an SMA node with id ``A`` -> ``"sma_A"``. Non-indicator nodes (or
    Stochastic, which has two buffers and is not a crossing operand source)
    return ``None`` so callers can no-op on an unresolvable operand. This is the
    bare-variable form (no shift, no ``:token``) used by ``_crossing.py`` which
    hand-appends its own ``[s]``/``[s+1]`` indices.
    """
    prefix = _BUFFER_PREFIX.get(_domain_type(node).lower())
    if prefix is None:
        return None
    return f"{prefix}{node_id(node)}"


# Legacy alias: the original public name. Kept so out-of-tree callers and the
# existing import surface stay byte-identical.
buffer_ref = buffer_var


def resolve_output(
    node: Node, output_id: str | None, shift: int
) -> str | None:
    """Resolve a node OUTPUT to its MQL5 read expression, or ``None``.

    Multi-output-aware resolver. The string shape is the cross-language contract
    between the editor operand picker, the crossing value-edges, and codegen:

    * ``output_id`` is ``None`` or ``"value"`` -> the PRIMARY output. Emits
      ``<prefix>_<id>[shift]`` with NO ``:token`` (so a single-output indicator
      at shift 0 reproduces today's ``<prefix>_<id>[0]`` byte-for-byte).
    * RSI's ``prev`` output -> ``<prefix>_<id>[shift+1]`` with NO ``:token``. This
      is a DELIBERATE special case: ``prev`` is "the previous bar of the same
      buffer", so it reads one bar further back on the SAME array rather than a
      distinct named buffer. (It is NOT the generic ``var:<outputId>[shift]``
      form.)
    * A NAMED non-primary output -> ``<prefix>_<id>:<outputId>[shift]``. This is
      DORMANT: no shipping indicator emits this form today.
    * Stochastic and every non-indicator node -> ``None`` (their codegen stays
      raw; e.g. Stochastic keeps emitting stochk_/stochd_ directly).

    An ``output_id`` that the node does not declare also yields ``None``.

    ZScore is resolved by a DEDICATED branch (isolated and early-returning) that
    emits EXPRESSIONS, not the array form: ``value`` -> ``z_<id>``;
    ``zabs`` -> ``MathAbs(z_<id>)``; ``zgt`` -> ``(z_<id> > 0)``;
    ``zlt`` -> ``(z_<id> < 0)``; and the array outputs zmean/zstd/zsma ->
    ``z<name>_<id>[shift]``. The sma/rsi/macd path below is left BYTE-IDENTICAL.
    """
    dtype = _domain_type(node).lower()
    if dtype == "zscore":
        return _resolve_zscore_output(node, output_id, shift)
    prefix = _BUFFER_PREFIX.get(dtype)
    outputs = _OUTPUTS.get(dtype)
    if prefix is None or outputs is None:
        return None  # Stochastic / non-indicator -> not resolver-routed.
    var = f"{prefix}{node_id(node)}"
    primary = outputs[0]
    # A None/"value"/primary request is the primary output: no ``:token``.
    if output_id is None or output_id == "value" or output_id == primary:
        return f"{var}[{shift}]"
    if output_id not in outputs:
        return None  # Unknown output id for this type.
    # RSI ``prev`` -> previous bar of the SAME buffer: read ``[shift+1]``,
    # token-free (a deliberate special case, not the generic named form).
    if dtype == "rsi" and output_id == "prev":
        return f"{var}[{shift + 1}]"
    return f"{var}:{output_id}[{shift}]"


def _resolve_zscore_output(
    node: Node, output_id: str | None, shift: int
) -> str | None:
    """Resolve a ZScore OUTPUT to its MQL5 EXPRESSION read, or ``None``.

    Unlike the array-based sma/rsi/macd path, ZScore's primary/scalar/signal
    outputs are local-scalar EXPRESSIONS computed in ``nodes/zscore.py``:

    * ``None`` / ``"value"`` -> ``z_<id>`` (the computed scalar; NO ``[shift]``).
    * ``"zabs"`` -> ``MathAbs(z_<id>)`` (operand-only |Z|; NO ``[shift]``).
    * ``"zgt"`` -> ``(z_<id> > 0)`` SIGNAL; ``"zlt"`` -> ``(z_<id> < 0)`` SIGNAL.
    * ``"zmean"`` / ``"zstd"`` / ``"zsma"`` -> ``z<name>_<id>[shift]`` ARRAY read.

    An unknown output id yields ``None``.
    """
    nid = node_id(node)
    if output_id is None or output_id == "value":
        return f"z_{nid}"
    if output_id == "zabs":
        return f"MathAbs(z_{nid})"
    if output_id == "zgt":
        return f"(z_{nid} > 0)"
    if output_id == "zlt":
        return f"(z_{nid} < 0)"
    prefix = _ZSCORE_ARRAY_PREFIX.get(output_id)
    if prefix is not None:
        return f"{prefix}{nid}[{shift}]"
    return None  # Unknown output id for ZScore.

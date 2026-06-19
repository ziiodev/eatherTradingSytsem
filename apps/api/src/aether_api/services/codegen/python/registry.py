"""Registry mapping node type -> Python per-node generator callable.

Mirrors the MQL5 ``services/codegen/registry.py`` 1:1 (same node-type keys, same
case-insensitive lookup) so the two engines have identical node coverage. Adding
a node type = importing its Python module and adding one entry here.
"""

from __future__ import annotations

from collections.abc import Callable

from aether_api.services.codegen.python.nodes import (
    bearish_cross,
    bullish_cross,
    buy,
    condition,
    end,
    log,
    logical_and,
    logical_not,
    logical_or,
    logical_xor,
    macd,
    risk_management,
    rsi,
    sell,
    sma,
    start,
    stochastic,
    zscore,
)
from aether_api.services.codegen.types import Connections, Node

GeneratorFn = Callable[[Node, Connections], str]

# Node type (as emitted by the frontend) -> Python generator function.
REGISTRY: dict[str, GeneratorFn] = {
    start.NODE_TYPE: start.generate,
    end.NODE_TYPE: end.generate,
    buy.NODE_TYPE: buy.generate,
    sell.NODE_TYPE: sell.generate,
    sma.NODE_TYPE: sma.generate,
    rsi.NODE_TYPE: rsi.generate,
    macd.NODE_TYPE: macd.generate,
    stochastic.NODE_TYPE: stochastic.generate,
    condition.NODE_TYPE: condition.generate,
    risk_management.NODE_TYPE: risk_management.generate,
    log.NODE_TYPE: log.generate,
    logical_and.NODE_TYPE: logical_and.generate,
    logical_or.NODE_TYPE: logical_or.generate,
    logical_not.NODE_TYPE: logical_not.generate,
    logical_xor.NODE_TYPE: logical_xor.generate,
    bullish_cross.NODE_TYPE: bullish_cross.generate,
    bearish_cross.NODE_TYPE: bearish_cross.generate,
    zscore.NODE_TYPE: zscore.generate,
}


def get_generator(node_type: str) -> GeneratorFn | None:
    """Return the Python generator for ``node_type``, or None if unknown.

    Lookup is case-insensitive against registered type names so the engine
    tolerates minor casing differences from the editor.
    """
    if node_type in REGISTRY:
        return REGISTRY[node_type]
    lowered = node_type.lower()
    for key, fn in REGISTRY.items():
        if key.lower() == lowered:
            return fn
    return None

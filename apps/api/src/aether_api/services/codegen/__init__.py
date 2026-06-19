"""Template-based, per-node MQL5 code generation engine.

Public entry point: `generate_mql5(graph, ea_name) -> str`.

Architecture: each node type lives in its own module under `nodes/` and exposes
`generate(node, connections) -> str`. A registry maps node type -> generator,
and the dispatcher (`generator.py`) walks the graph and assembles the full EA.
Adding a node type means adding one module and registering it — never editing a
central switch.
"""

from aether_api.services.codegen.generator import generate_mql5

__all__ = ["generate_mql5"]

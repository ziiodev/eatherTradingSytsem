"""Pure-stdlib Python code generation engine.

Public entry point: ``generate_python(graph, ea_name) -> str``. Mirrors the MQL5
engine's per-node, registry-driven architecture and REUSES the shared graph layer
(``services/codegen/graph.py`` + ``copy_depth.py``).
"""

from aether_api.services.codegen.python.generator import generate_python

__all__ = ["generate_python"]

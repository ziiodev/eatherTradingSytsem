"""Small helpers shared by the Python per-node generators.

Reuses the language-agnostic ``param``/``node_id`` from the MQL5 helpers (a node
dict is a node dict regardless of target language) and adds Python-specific
indentation + comment utilities. The Python on_bar body is indented ONE level
(4 spaces) inside ``on_bar``; the compute_indicators body is also one level.
"""

from __future__ import annotations

from aether_api.services.codegen.helpers import node_id, param

__all__ = ["IND", "comment", "node_id", "param", "py_id"]

# One level of function-body indentation in the emitted script.
IND = "    "


def py_id(node: dict) -> str:
    """Return a Python-identifier-safe form of the node id.

    Node ids from the editor are arbitrary strings (e.g. ``n1``); they are used
    only as dict KEYS in the emitted script (``ind["sma_n1"]``), so any string is
    safe. This wrapper exists so a future id-sanitization rule has one home.
    """
    return node_id(node)


def comment(node: dict, label: str) -> str:
    """Return a one-line Python comment identifying the node (on_bar level)."""
    return f"{IND}# [{label}] node id={node_id(node)}"

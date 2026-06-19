"""Translate a Condition operand STRING into its real MQL5 read.

A Condition operand is a free-form string typed/picked in the editor. Most
operands are LITERALS (``30``, ``1.2345``, a custom expression) or PLAIN
indicator refs (``rsi_<id>[0]``) that already ARE valid MQL5 — those must pass
through string-identical so every existing graph stays byte-identical.

The ONLY operands this module rewrites are those carrying an ``:outputId`` token
in the foundation grammar ``<prefix>_<id>:<outputId>[shift]``. That token names a
NON-primary output of a multi-output indicator (e.g. RSI's ``prev``); it is NOT
valid MQL5 on its own, so it MUST be resolved through :func:`resolve_output` to
the indicator's actual buffer read before interpolation into the guard.

The regex gate below is the SOLE guarantee of plain-ref / literal byte-identity:
if an operand does not match the ``:outputId``-bearing shape, it is returned
UNCHANGED.
"""

from __future__ import annotations

import re

from aether_api.services.codegen.buffer_ref import resolve_output
from aether_api.services.codegen.types import Node

# Matches ONLY operands that carry an ``:outputId`` token:
#   <prefix>_<id>:<outputId>[<shift>]
# Groups: node var prefix+id (e.g. ``rsi_7``), the output id, the integer shift.
# A plain ref ``rsi_7[0]`` (no ``:``) deliberately does NOT match, so it falls
# through to the string-identical return below. Anchored end-to-end so a partial
# match inside a larger custom expression is never rewritten.
_OUTPUT_REF_RE = re.compile(
    r"^(?P<prefix>[a-zA-Z]+)_(?P<id>[^\[:\]]+):(?P<output>[A-Za-z0-9_]+)"
    r"\[(?P<shift>\d+)\]$"
)


def translate_operand(operand: str, nodes_by_id: dict[str, Node]) -> str:
    """Return the MQL5 read for ``operand``, rewriting ONLY ``:outputId`` refs.

    * Operand matches ``<prefix>_<id>:<outputId>[shift]`` AND ``<id>`` resolves to
      a known node whose :func:`resolve_output` yields a read -> return that read.
    * Anything else — a plain ``<prefix>_<id>[k]`` ref, a literal number, a custom
      string, an operand with a stale/unknown id, or an output id the node does
      not declare — is returned UNCHANGED (string-identical).
    """
    match = _OUTPUT_REF_RE.match(operand)
    if match is None:
        return operand  # No :outputId token -> pass through byte-identical.
    node = nodes_by_id.get(match.group("id"))
    if node is None:
        return operand  # Stale/unknown id -> leave the operand untouched.
    resolved = resolve_output(node, match.group("output"), int(match.group("shift")))
    if resolved is None:
        return operand  # Output id not declared by this node -> untouched.
    return resolved

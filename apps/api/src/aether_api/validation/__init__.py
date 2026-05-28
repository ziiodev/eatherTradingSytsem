"""Validation primitives — pure shape checks, no side effects.

Modules in here MUST NOT execute user-provided code. ``logica.py``
parses Python source with ``ast.parse`` (which is safe — it builds an
AST, it does not run the program). Anything heavier (sandboxed
execution, type inference) lives in a separate change.
"""

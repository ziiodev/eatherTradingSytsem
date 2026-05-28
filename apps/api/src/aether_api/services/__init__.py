"""Service layer — pure-Python (or lightweight) primitives without DB session lifecycle.

Each module here MUST stay free of router / DB / transaction concerns so it
can be reused in workers, tests, and future bots without ceremony.
"""

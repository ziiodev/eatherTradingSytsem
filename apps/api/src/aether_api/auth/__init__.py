"""Authentication subsystem.

Layout:

* :mod:`aether_api.auth.passwords`   — argon2id hashing.
* :mod:`aether_api.auth.tokens`      — JWT access + opaque refresh.
* :mod:`aether_api.auth.cookies`     — cookie writers / readers.
* :mod:`aether_api.auth.routes`      — APIRouter under ``/api/auth``.

Nothing under ``auth/`` is allowed to write a token (JWT or refresh)
to a logger. Treat secrets as toxic at the type level.
"""

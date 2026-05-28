"""Async Docker Engine HTTP client — talks to the docker-socket-proxy.

The client speaks the Engine HTTP API at ``settings.docker_host``
(default ``tcp://docker-proxy:2375``). The API process is wired to the
``docker-proxy`` service name on the ``aether-internal`` compose bridge;
the proxy is the only thing that mounts ``/var/run/docker.sock``, with a
restrictive allowlist (CONTAINERS / IMAGES / BUILD only).

The :func:`get_docker` helper returns a process-wide :class:`aiodocker.Docker`
singleton. We deliberately do NOT cache via :func:`functools.lru_cache`
because the underlying ``aiohttp.ClientSession`` is bound to the running
event loop — a cache that survives event-loop swaps (e.g. between
``pytest-asyncio`` tests) would leak a stale session.

The lifespan hook in ``aether_api.main`` is responsible for calling
:func:`close_docker` on shutdown so the underlying aiohttp session is
released cleanly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aether_api.core.settings import get_settings

if TYPE_CHECKING:  # pragma: no cover — typing only
    import aiodocker  # noqa: F401

# ``Any`` because aiodocker is an optional / late-binding dependency: we
# don't want to force the import at module load (so tests without
# aiodocker can still import this module). The functions below add the
# real type at call time through the local ``import aiodocker``.
_client: Any | None = None


def get_docker() -> Any:
    """Return the lazily-initialised :class:`aiodocker.Docker` singleton.

    Side effects: opens an aiohttp ClientSession on first call. The
    session is bound to whichever event loop is running at call time —
    callers in long-lived workers should rely on the FastAPI lifespan to
    own the singleton.
    """
    global _client
    if _client is None:
        import aiodocker  # local import — keeps the optional dep optional

        url = get_settings().docker_host
        # aiodocker.Docker accepts the same URL form as the docker CLI's
        # DOCKER_HOST env var (``tcp://host:port`` for HTTP, ``unix://...``
        # for a socket). The proxy sidecar listens on TCP only.
        _client = aiodocker.Docker(url=url)
    return _client


async def close_docker() -> None:
    """Tear down the singleton — called from the FastAPI lifespan shutdown.

    Safe to call when no client has been created (no-op).
    """
    global _client
    if _client is not None:
        try:
            await _client.close()
        finally:
            _client = None


def reset_for_tests() -> None:
    """Force the singleton to re-create on next ``get_docker()`` call.

    Tests that swap settings (e.g. point ``docker_host`` at a stub) MUST
    call this after ``get_settings.cache_clear()`` so the next consumer
    sees the new URL.
    """
    global _client
    _client = None


__all__ = ["close_docker", "get_docker", "reset_for_tests"]

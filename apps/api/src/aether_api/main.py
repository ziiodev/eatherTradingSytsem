"""FastAPI application entrypoint.

Mounts:

* ``GET  /healthz``                — liveness probe.
* ``GET  /.well-known/jwks.json``  — RS256 public key set (see :mod:`aether_api.auth.jwks`).
* ``/api/auth/*``                  — see :mod:`aether_api.auth.routes`.
* ``/api/me/*``                    — see :mod:`aether_api.routers.me`.
* ``/api/me/mfa/*``                — see :mod:`aether_api.routers.me_mfa`.
* ``/api/me/audit-log``            — see :mod:`aether_api.routers.audit_log`.
* ``/api/projects/*``              — see :mod:`aether_api.routers.projects`.
* ``/api/agents/*``                — see :mod:`aether_api.routers.agents`.
* ``/api/skills/*``                — see :mod:`aether_api.routers.skills`.
* ``/api/tools/*``                 — see :mod:`aether_api.routers.tools`.
* OpenAPI at ``/openapi.json`` (default location).

Run with::

    aether-api          # uses [project.scripts] entry
    # or
    uvicorn aether_api.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from aether_api import __version__
from aether_api.auth.jwks import router as jwks_router
from aether_api.auth.routes import router as auth_router
from aether_api.core.logging import setup_logging
from aether_api.core.middleware import RequestIDMiddleware
from aether_api.core.observability import init_observability
from aether_api.core.settings import get_settings
from aether_api.routers.agents import router as agents_router
from aether_api.routers.audit_log import router as audit_log_router
from aether_api.routers.me import router as me_router
from aether_api.routers.me_mfa import router as me_mfa_router
from aether_api.routers.projects import router as projects_router
from aether_api.routers.projects_live import router as projects_live_router
from aether_api.routers.skills import router as skills_router
from aether_api.routers.tools import router as tools_router
from aether_api.sleep.routes import (
    config_versions_router as sleep_config_versions_router,
)
from aether_api.sleep.routes import (
    projects_sleep_router as sleep_projects_router,
)

#: Hard cap on the Content-Length of any request to an ``/api/agents``
#: write endpoint. 320 KiB comfortably accommodates the 256 KiB ``logica``
#: payload cap (see :mod:`aether_api.validation.logica`) plus envelope
#: (Pydantic JSON, name/description/entrypoint fields). Anything bigger is
#: refused BEFORE the body is read — this is the cheap perimeter defence;
#: the field-level cap in ``validate_logica_shape`` is the precise one.
AGENT_WRITE_BODY_LIMIT_BYTES: Final[int] = 320 * 1024

#: Paths whose write methods are body-size-guarded. Listed explicitly so the
#: guard never accidentally fires on auth or projects endpoints, which have
#: their own (smaller) field budgets owned by their own changes.
_AGENT_WRITE_PATH_PREFIX: Final[str] = "/api/agents"
_AGENT_WRITE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PATCH"})


class AgentBodySizeGuardMiddleware(BaseHTTPMiddleware):
    """Reject oversize agent writes with HTTP 413 BEFORE consuming the body.

    Reading ``Content-Length`` is cheap (it's a header) and lets us send a
    structured 413 response without ever pulling the body off the wire. A
    client that lies about ``Content-Length`` (e.g. chunked transfer with
    no header) will fall through to the per-field cap inside
    ``validate_logica_shape``, which catches the same case at field
    granularity.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        # Only guard the agents write surface — everything else is
        # passed through unchanged.
        if (
            request.method in _AGENT_WRITE_METHODS
            and request.url.path.startswith(_AGENT_WRITE_PATH_PREFIX)
        ):
            raw = request.headers.get("content-length")
            if raw is not None:
                try:
                    length = int(raw)
                except ValueError:
                    length = -1
                if length > AGENT_WRITE_BODY_LIMIT_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={
                            "detail": "request body too large",
                            "max_bytes": AGENT_WRITE_BODY_LIMIT_BYTES,
                        },
                    )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hook.

    * Startup:
        1. configure structlog (drives stdout JSON for app + uvicorn);
        2. fire the optional observability bootstrap (Sentry / OTel /
           Prometheus — each guarded by its own env flag);
        3. when ``settings.docker_reconcile_enabled`` is True, run one
           boot-time reconciliation sweep and schedule the periodic
           ticker.
    * Shutdown: signal the reconciler ticker to stop, close the
      aiodocker singleton. The DB engine is per-process and
      garbage-collected on exit.
    """
    import asyncio
    import contextlib

    settings = get_settings()
    setup_logging(settings.log_level)
    init_observability(app, settings)

    reconcile_task: asyncio.Task[None] | None = None
    stop_event = asyncio.Event()
    sleep_scheduler = None

    # Sleep Phase boot sweep — mark crashed runs from a previous process
    # as ``crashed`` and restore project status. Cheap and always-on
    # because the predicate is "running & started_at < cutoff" and only
    # rewrites already-stale rows.
    try:
        from aether_api.db.session import get_session_maker
        from aether_api.sleep.boot_sweep import recover_stale_runs

        session_maker = get_session_maker()
        async with session_maker() as boot_session:
            await recover_stale_runs(boot_session)
    except Exception:  # noqa: BLE001 — boot sweep is best-effort.
        import logging as _logging

        _logging.getLogger(__name__).exception(
            "sleep.boot_sweep: recovery raised; continuing startup"
        )

    # Sleep Phase scheduler — feature-flagged, defaults off.
    if settings.sleep_scheduler_enabled:
        try:
            from aether_api.sleep.scheduler import (
                _build_scheduler,
                start_scheduler,
            )

            sleep_scheduler = _build_scheduler()
            await start_scheduler(sleep_scheduler)
        except Exception:  # noqa: BLE001
            import logging as _logging

            _logging.getLogger(__name__).exception(
                "sleep.scheduler: startup raised; continuing without scheduler"
            )
            sleep_scheduler = None

    if settings.docker_reconcile_enabled:
        # Local imports so a bootstrap env without aiodocker still
        # imports main cleanly when the flag is off.
        from aether_api.docker_control.client import close_docker
        from aether_api.docker_control.reconcile import run_ticker, sweep_once

        # Don't block startup if the proxy is briefly unreachable.
        with contextlib.suppress(Exception):
            await sweep_once()
        reconcile_task = asyncio.create_task(
            run_ticker(stop_event), name="docker-reconcile"
        )

        try:
            yield
        finally:
            stop_event.set()
            if reconcile_task is not None:
                try:
                    await asyncio.wait_for(reconcile_task, timeout=5.0)
                except (TimeoutError, asyncio.CancelledError):
                    reconcile_task.cancel()
            await close_docker()
            if sleep_scheduler is not None:
                from aether_api.sleep.scheduler import shutdown_scheduler

                await shutdown_scheduler(sleep_scheduler)
    else:
        try:
            yield
        finally:
            if sleep_scheduler is not None:
                from aether_api.sleep.scheduler import shutdown_scheduler

                await shutdown_scheduler(sleep_scheduler)


def create_app() -> FastAPI:
    """Build the FastAPI app. Factored out for tests / lifespan rebinding."""
    settings = get_settings()

    app = FastAPI(
        title="Aether API",
        version=__version__,
        lifespan=lifespan,
        # Default openapi_url stays /openapi.json — explicitly set so a
        # future "lock down OpenAPI in prod" change has one place to edit.
        openapi_url="/openapi.json",
    )

    # CORS — must allow credentials so cookies travel cross-origin
    # during local dev (frontend at :3000 → API at :8000).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # Body-size guard for /api/agents POST/PATCH — rejects 413 before
    # the body is read. Field-level cap on `logica` lives in
    # ``aether_api.validation.logica``.
    app.add_middleware(AgentBodySizeGuardMiddleware)

    # Request-ID + structlog contextvars binding. Starlette mounts
    # middlewares in REVERSE order — the LAST add_middleware call is
    # the OUTER layer. We want X-Request-ID to be the outermost wrapper
    # so every other layer (CORS, body-size guard, exception handlers)
    # logs against the same request_id.
    app.add_middleware(RequestIDMiddleware)

    # Routers
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(me_mfa_router)
    app.include_router(audit_log_router)
    app.include_router(projects_router)
    # Live MT5 surface (mt5-integration change). Same /api/projects prefix;
    # FastAPI routes both routers under the same path tree.
    app.include_router(projects_live_router)
    app.include_router(agents_router)
    app.include_router(skills_router)
    # One-shot tools (MQL5→Py translator). Feature-flagged inside the
    # router; mounting is unconditional so the endpoint shape never
    # depends on env flags.
    app.include_router(tools_router)
    # Sleep Phase — manual trigger / runs feed (per-project) and
    # config_versions approve/reject/revert. Mounted regardless of the
    # AETHER_SLEEP_SCHEDULER_ENABLED flag so manual operator triggers
    # always work.
    app.include_router(sleep_projects_router)
    app.include_router(sleep_config_versions_router)
    # JWKS — published at the canonical /.well-known location for sister
    # services and the edge middleware to verify RS256 access tokens.
    app.include_router(jwks_router)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, object]:
        return {"ok": True, "version": __version__}

    return app


# Module-level app for ``uvicorn aether_api.main:app``.
app = create_app()


def run() -> None:
    """``aether-api`` entry point — boots uvicorn with dev defaults.

    Production should invoke uvicorn (or gunicorn+uvicorn workers)
    directly with its own flags; this exists for ``uv run aether-api``
    during local development.
    """
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "aether_api.main:app",
        host="0.0.0.0",  # noqa: S104 — dev binding only; reverse proxy in prod
        port=8000,
        reload=settings.environment == "dev",
        log_config=None,  # we own logging — see core.logging.setup_logging
    )

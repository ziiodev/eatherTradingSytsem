"""Optional observability bootstrap — Sentry, OpenTelemetry, Prometheus.

Every component is **feature-flagged at runtime**. An unconfigured
environment (no ``SENTRY_DSN`` / ``OTEL_EXPORTER_OTLP_ENDPOINT`` /
``METRICS_ENABLED=false``) takes the no-op path and the FastAPI app
behaves exactly like the unobserved bootstrap.

Why a separate module:

* Keeps :mod:`aether_api.main` free of import-heavy SDKs that we only
  load when configured.
* Lets the test suite import :mod:`aether_api.main` without Sentry /
  OTel side effects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import structlog

from aether_api.core.pii import scrub_event
from aether_api.core.settings import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------
def init_sentry(settings: Settings) -> bool:
    """Initialise the Sentry SDK if ``SENTRY_DSN`` is set. Returns True on init.

    Tracing is delegated to OpenTelemetry — Sentry's own tracer is left
    at sample rate 0.0 to avoid double-counting spans. ``before_send`` runs
    the shared PII scrubber so forbidden keys / JWT-shaped strings never
    leave the box.
    """
    if not settings.sentry_dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:  # pragma: no cover — dep is declared
        log.warning("sentry.disabled", reason="sentry_sdk not installed")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        release=settings.sentry_release,
        environment=settings.sentry_environment or settings.environment,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        # OTel owns tracing — leave Sentry's own tracer off so spans aren't
        # double-counted. Errors still flow through normally.
        traces_sample_rate=0.0,
        # Strip PII before the event leaves the process. The signature is
        # widened with ``type: ignore`` because Sentry's ``Event`` is a
        # ``TypedDict`` and our scrubber works on plain ``dict``; the runtime
        # shape is identical.
        before_send=scrub_event,  # type: ignore[arg-type]
        # Don't auto-attach request bodies; we have explicit audit rows for
        # state changes that matter and don't want incidental PII shipped.
        send_default_pii=False,
    )
    log.info("sentry.enabled", environment=settings.sentry_environment or settings.environment)
    return True


# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
def init_tracing(app: FastAPI, settings: Settings) -> bool:
    """Initialise the OTel SDK + FastAPI/SQLAlchemy instrumentors.

    Returns True iff ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set AND the SDK
    imports succeed. We log + skip rather than raise so a partial
    observability stack (e.g. only Sentry configured) still boots.
    """
    if not settings.otel_exporter_otlp_endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # pragma: no cover — deps are declared
        log.warning("otel.disabled", reason="opentelemetry sdk not installed")
        return False

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.0.1",
            "deployment.environment": settings.environment,
        }
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        )
    )
    trace.set_tracer_provider(provider)

    # FastAPI instrumentation auto-creates a server span per request and
    # pulls the request id from the W3C ``traceparent`` header — which
    # composes cleanly with our X-Request-ID middleware (different IDs,
    # different purposes).
    FastAPIInstrumentor.instrument_app(app)

    # SQLAlchemy instrumentor needs the sync engine (the async engine has
    # a ``sync_engine`` attribute pointing at it).
    from aether_api.db.session import get_engine

    engine = get_engine()
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        enable_commenter=True,
    )

    log.info("otel.enabled", endpoint=settings.otel_exporter_otlp_endpoint)
    return True


# ---------------------------------------------------------------------------
# Prometheus / metrics
# ---------------------------------------------------------------------------
def init_metrics(app: FastAPI, settings: Settings) -> bool:
    """Mount ``/metrics`` (admin-gated) backed by prometheus-fastapi-instrumentator.

    The endpoint is added at the FastAPI level rather than the
    instrumentator's default ``add_metrics_route`` so we can layer
    :func:`aether_api.tenancy.middleware.admin_required` on it.
    """
    if not settings.metrics_enabled:
        return False

    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError:  # pragma: no cover — dep is declared
        log.warning("metrics.disabled", reason="prometheus_fastapi_instrumentator not installed")
        return False

    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/metrics", "/healthz"],
    )
    instrumentator.instrument(app)

    # Admin-gated exposition endpoint. Defined inline so the instrumentator
    # collector picks it up after instrumentation.
    from fastapi import Depends, Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from aether_api.tenancy.middleware import admin_required

    @app.get("/metrics", include_in_schema=False)
    async def metrics(_admin: object = Depends(admin_required)) -> Response:
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    log.info("metrics.enabled")
    return True


def _quiet_noisy_loggers() -> None:
    """Tame chatty third-party loggers so production stdout stays readable.

    Called from :func:`init_observability` regardless of which components
    end up active — the rule is "even without observability we don't want
    libraries spamming DEBUG by accident".
    """
    for name in ("urllib3", "asyncio", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)


def init_observability(app: FastAPI, settings: Settings) -> None:
    """One-shot bootstrap called from the FastAPI lifespan.

    The order matters slightly:

    1. Sentry first — so any errors raised during the OTel / metrics init
       are still captured.
    2. OTel — wires the instrumentors before the first request lands.
    3. Metrics — registers the ``/metrics`` route.
    """
    _quiet_noisy_loggers()
    init_sentry(settings)
    init_tracing(app, settings)
    init_metrics(app, settings)

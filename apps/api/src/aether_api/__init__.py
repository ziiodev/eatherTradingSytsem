"""Aether Trading System — FastAPI backend package.

Public surface (Phase 3):

* :func:`aether_api.main.create_app` — build the FastAPI app.
* :func:`aether_api.main.run`        — uvicorn entry point.
* :mod:`aether_api.models`           — SQLAlchemy 2.0 ORM models.
* :mod:`aether_api.core.settings`    — pydantic-settings loader.
"""

__version__ = "0.0.1"

"""SQLAlchemy declarative base.

Every model in :mod:`aether_api.models` MUST inherit from :class:`Base`
defined here. Alembic's ``env.py`` imports ``Base.metadata`` as
``target_metadata`` so future autogenerate runs see the same metadata
the application uses at runtime.

A naming convention is pinned for indexes/constraints — without it,
SQLAlchemy generates anonymous names which Alembic autogenerate then
diffs as "drop + recreate" on every run, producing noise.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Postgres convention — matches the names used by the hand-written
# 0001_init migration (e.g. ``users_email_lower``, ``idx_users_active``).
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)

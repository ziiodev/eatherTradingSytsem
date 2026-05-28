"""Shared repository base.

The :meth:`_for_user` helper is the *single primitive* every user-scoped
query goes through. Centralising the filter clause here makes audits
easy: ``grep -rn "_for_user(" apps/api/src/aether_api/repositories`` is
the exhaustive list of tenant-scoped reads.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.sql import Select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Holds the per-request :class:`AsyncSession` and tenant primitives."""

    #: Subclasses override this with their ORM model class. Used by
    #: :meth:`_for_user` to add the WHERE clause without each subclass
    #: re-typing the model name.
    model: Any = None

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _for_user(self, stmt: Select[Any], user_id: uuid.UUID) -> Select[Any]:
        """Append the ``WHERE model.user_id = :user_id`` tenant filter.

        Raises if the subclass forgot to set :attr:`model` — failing
        loudly is preferable to silently returning all rows.
        """
        if self.model is None:
            raise RuntimeError(
                f"{self.__class__.__name__}.model is not set — "
                "every tenant-scoped repository must declare its ORM model"
            )
        return stmt.where(self.model.user_id == user_id)

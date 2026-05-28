"""Repository layer.

Every user-scoped query MUST flow through a repository that applies the
``user_id`` tenant filter. Handlers MUST NOT issue raw SELECTs against
user-scoped tables — the discipline is what keeps the cross-tenant
isolation invariant intact at the data-access layer (defense in depth).
"""

from aether_api.repositories.agent_repository import AgentRepository
from aether_api.repositories.base import BaseRepository
from aether_api.repositories.project_repository import ProjectRepository
from aether_api.repositories.session_repository import SessionRepository
from aether_api.repositories.user_repository import UserRepository

__all__ = [
    "AgentRepository",
    "BaseRepository",
    "ProjectRepository",
    "SessionRepository",
    "UserRepository",
]

"""Repository layer.

Every user-scoped query MUST flow through a repository that applies the
``user_id`` tenant filter. Handlers MUST NOT issue raw SELECTs against
user-scoped tables — the discipline is what keeps the cross-tenant
isolation invariant intact at the data-access layer (defense in depth).
"""

from aether_api.repositories.account_repository import AccountRepository
from aether_api.repositories.agent_repository import AgentRepository
from aether_api.repositories.base import BaseRepository
from aether_api.repositories.exchange_repository import ExchangeRepository
from aether_api.repositories.pair_repository import PairRepository
from aether_api.repositories.session_repository import SessionRepository
from aether_api.repositories.user_repository import UserRepository

__all__ = [
    "AccountRepository",
    "AgentRepository",
    "BaseRepository",
    "ExchangeRepository",
    "PairRepository",
    "SessionRepository",
    "UserRepository",
]

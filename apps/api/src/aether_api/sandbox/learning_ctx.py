"""Sandbox-side learning proxies — the agent's ONLY view of the learning substrate.

The sandboxed child process MUST NOT carry a DB session, a repository,
or any object that holds host file descriptors. Instead, the child gets
three small, frozen, pickle-safe **proxies** that route every call back
to the parent process through an RPC channel (a duplex
``multiprocessing.Connection`` pair created by the engine).

The three proxies and their contract:

* :class:`QTableProxy` — READ-ONLY. ``.get(state)`` returns the action→Q dict
  for ``state_key(state)`` (or ``None``); ``.suggest(state)`` returns the
  argmax action key (or ``None``).
* :class:`SemanticProxy` — READ-ONLY. ``.list(rule_type=None, active=True)``
  returns a list of frozen :class:`Rule` dataclasses.
* :class:`EpisodicProxy` — WRITE-ONLY. ``.record(state, action, reward, result,
  reasoning, q_before=None)`` inserts one episode and returns a frozen
  :class:`EpisodeRef`.

Hard constraints enforced here:

1. **Frozen identity.** ``user_id`` and ``project_id`` are bound at
   construction (frozen dataclass). The agent CANNOT mutate them; an
   attempt raises ``dataclasses.FrozenInstanceError``.

2. **Defence in depth.** Even if a future bug let the child re-assign
   the field, the parent-side dispatcher (see :mod:`aether_api.sandbox.rpc`)
   IGNORES any ``user_id`` / ``project_id`` in the child's payload and
   uses the bound tuple it itself recorded. The handlers never trust
   the child for tenancy.

3. **Pickle-safe.** Each proxy holds only:
   * an :class:`RpcClient` (which wraps a ``multiprocessing.Connection``;
     Connections survive ``spawn`` handle inheritance), and
   * two ``str`` UUIDs.
   No SQLAlchemy objects, no sessions, no open sockets.

4. **No setter / no delete on any read proxy.** ``QTableProxy.set(...)``
   and ``SemanticProxy.deactivate(...)`` simply do not exist. Episodic
   exposes ONLY ``.record``; there is no delete vector.

The NO-OP variants (:class:`NoopQTable`, :class:`NoopSemantic`,
:class:`NoopEpisodic`) are wired in when ``AETHER_LEARNING_ENABLED`` is
``"false"``. Reads return ``None`` / ``[]``; ``EpisodicProxy.record``
raises ``RuntimeError("learning disabled")``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "EpisodeRef",
    "EpisodicProxy",
    "LearningProxies",
    "NoopEpisodic",
    "NoopQTable",
    "NoopSemantic",
    "QTableProxy",
    "Rule",
    "RpcClientProtocol",
    "SemanticProxy",
]


# ---------------------------------------------------------------------------
# Wire-friendly value types — frozen dataclasses, pickle-safe.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One semantic rule, projected to plain types.

    Mirrors the dict shape produced by
    :func:`aether_api.learning.recovery._rule_to_dict` but as a frozen
    dataclass so agent code sees a stable attribute surface and cannot
    mutate the cached view.
    """

    id: str
    rule_type: str
    title: str
    content: str
    confidence: float
    source: str
    version: int
    active: bool


@dataclass(frozen=True)
class EpisodeRef:
    """Receipt returned by :meth:`EpisodicProxy.record`.

    ``id`` is the newly minted UUID for the persisted ``episodic_memory``
    row. ``sleep_run_id`` echoes the ``consumed_by_sleep_run_id`` column
    (``None`` for episodes recorded outside a Sleep Phase). ``q_value_before``
    is forwarded as-is so the caller can confirm the episode was stored
    with the value it sent.
    """

    id: uuid.UUID
    sleep_run_id: uuid.UUID | None
    q_value_before: float | None


# ---------------------------------------------------------------------------
# RPC client protocol — the proxies depend on the interface, not the impl.
# ---------------------------------------------------------------------------


class RpcClientProtocol(Protocol):
    """Synchronous request/response over the parent ↔ child pipe.

    The concrete implementation lives in :mod:`aether_api.sandbox.rpc` and
    is constructed by the child bootstrap from the duplex Connection
    handed in by the engine. A ``Protocol`` keeps this module
    test-friendly (a fake client can satisfy the surface without
    importing the real one) and avoids circular imports.

    ``call(method, **kwargs)`` MUST be synchronous and MUST raise
    :class:`RuntimeError` (or a subclass) on parent-side failures so the
    agent code can catch a single, predictable exception type.
    """

    def call(self, method: str, /, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Tenancy helpers.
# ---------------------------------------------------------------------------


def _validate_state(state: Any) -> dict[str, Any]:
    """Confirm ``state`` is a plain dict the child can safely send.

    The actual state-key canonicalisation runs parent-side via
    :func:`aether_api.learning.state_key` — the child does not import
    ``hashlib``/``json`` here because the sandbox allowlist is what
    decides import surface, not this module. Forwarding the raw dict
    keeps the wire format obvious.
    """
    if not isinstance(state, dict):
        raise TypeError(f"state must be a dict, got {type(state).__name__}: {state!r}")
    return state


# ---------------------------------------------------------------------------
# Q-Table proxy — READ-ONLY.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QTableProxy:
    """Read-only window onto the project's current Q-Table.

    Two methods, both READ:

    * :meth:`get` — return ``{action: q_value, ...}`` for the given state,
      or ``None`` when the state is unknown / the project has no Q-Table.
    * :meth:`suggest` — return the action with the highest Q for the
      state, or ``None``. Tie-breaks are resolved alphabetically by
      action key (deterministic; same rule as the parent-side helpers).

    There is NO ``set`` / ``put`` / ``update`` method by design — Q-Table
    promotion is the Sleep Phase's job, never the Worker's.
    """

    _rpc: RpcClientProtocol
    user_id: str
    project_id: str

    def get(self, state: dict[str, Any]) -> dict[str, float] | None:
        """Return the action→Q-value mapping for ``state`` (or ``None``)."""
        state = _validate_state(state)
        raw: Any = self._rpc.call(
            "qtable.get",
            state=state,
        )
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        return {str(k): float(v) for k, v in raw.items()}

    def suggest(self, state: dict[str, Any]) -> str | None:
        """Return the argmax action key for ``state`` (or ``None``)."""
        state = _validate_state(state)
        raw: Any = self._rpc.call(
            "qtable.suggest",
            state=state,
        )
        if raw is None:
            return None
        return str(raw)


# ---------------------------------------------------------------------------
# Semantic proxy — READ-ONLY.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticProxy:
    """Read-only listing of the project's active semantic rules."""

    _rpc: RpcClientProtocol
    user_id: str
    project_id: str

    def list(
        self,
        rule_type: str | None = None,
        active: bool = True,
    ) -> list[Rule]:
        """Return active rules for the project as frozen :class:`Rule` objects.

        ``active=False`` is honoured at the parent — the agent CAN ask
        for the supersession history but cannot mutate it.
        """
        raw: list[dict[str, Any]] = self._rpc.call(
            "semantic.list",
            rule_type=rule_type,
            active=active,
        )
        return [
            Rule(
                id=str(item["id"]),
                rule_type=str(item["rule_type"]),
                title=str(item.get("title") or ""),
                content=str(item.get("content") or ""),
                confidence=float(item.get("confidence") or 0.0),
                source=str(item.get("source") or ""),
                version=int(item.get("version") or 1),
                active=bool(item.get("active", True)),
            )
            for item in raw
        ]


# ---------------------------------------------------------------------------
# Episodic proxy — WRITE-ONLY (record). No read, no delete.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodicProxy:
    """Write-only sink for (state, action, reward, result, reasoning)."""

    _rpc: RpcClientProtocol
    user_id: str
    project_id: str

    def record(
        self,
        state: dict[str, Any],
        action: str,
        reward: float,
        result: dict[str, Any],
        reasoning: str,
        q_before: float | None = None,
    ) -> EpisodeRef:
        """Persist one episode via the parent-side repository.

        Returns an :class:`EpisodeRef` carrying the newly minted row id.
        The bound ``user_id`` / ``project_id`` are NOT included in the
        payload — the parent-side handler uses its own bound copy so a
        tampered child cannot point a write at a foreign project.
        """
        state = _validate_state(state)
        if not isinstance(action, str) or not action:
            raise TypeError(f"action must be a non-empty str, got {action!r}")
        if not isinstance(reasoning, str):
            raise TypeError(f"reasoning must be a str, got {type(reasoning).__name__}")
        raw: dict[str, Any] = self._rpc.call(
            "episodic.record",
            state=state,
            action=action,
            reward=float(reward),
            result=result,
            reasoning=reasoning,
            q_before=None if q_before is None else float(q_before),
        )
        return EpisodeRef(
            id=uuid.UUID(str(raw["id"])),
            sleep_run_id=(
                None if raw.get("sleep_run_id") is None else uuid.UUID(str(raw["sleep_run_id"]))
            ),
            q_value_before=(
                None if raw.get("q_value_before") is None else float(raw["q_value_before"])
            ),
        )


# ---------------------------------------------------------------------------
# NO-OP variants — wired in when AETHER_LEARNING_ENABLED=false.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoopQTable:
    """Drop-in replacement when learning is disabled.

    Reads return ``None``; there are no other methods, so any attempt to
    mutate the table from agent code raises ``AttributeError`` (same
    surface as :class:`QTableProxy`).
    """

    user_id: str = ""
    project_id: str = ""

    def get(self, state: dict[str, Any]) -> dict[str, float] | None:  # noqa: ARG002
        return None

    def suggest(self, state: dict[str, Any]) -> str | None:  # noqa: ARG002
        return None


@dataclass(frozen=True)
class NoopSemantic:
    """Drop-in replacement when learning is disabled.

    ``.list`` returns an empty list regardless of arguments. Frozen so
    agent code cannot rebind ``user_id`` / ``project_id``.
    """

    user_id: str = ""
    project_id: str = ""

    def list(
        self,
        rule_type: str | None = None,  # noqa: ARG002
        active: bool = True,  # noqa: ARG002
    ) -> list[Rule]:
        return []


@dataclass(frozen=True)
class NoopEpisodic:
    """Drop-in replacement when learning is disabled.

    ``.record`` raises ``RuntimeError("learning disabled")`` so the
    Worker fails loudly rather than silently dropping episodes — silent
    drop would corrupt the Sleep Phase synthesis.
    """

    user_id: str = ""
    project_id: str = ""

    def record(
        self,
        state: dict[str, Any],  # noqa: ARG002
        action: str,  # noqa: ARG002
        reward: float,  # noqa: ARG002
        result: dict[str, Any],  # noqa: ARG002
        reasoning: str,  # noqa: ARG002
        q_before: float | None = None,  # noqa: ARG002
    ) -> EpisodeRef:
        raise RuntimeError("learning disabled")


# ---------------------------------------------------------------------------
# Bundle that the engine attaches to AgentContext.
# ---------------------------------------------------------------------------


@dataclass
class LearningProxies:
    """Container the engine sets on ``ctx`` just before user code runs.

    Kept as a mutable dataclass on the outer surface so the engine can
    swap in / out the Noop variants without re-instantiating the whole
    bundle; the proxies INSIDE are still frozen.
    """

    qtable: QTableProxy | NoopQTable = field(default_factory=NoopQTable)
    semantic: SemanticProxy | NoopSemantic = field(default_factory=NoopSemantic)
    episodic: EpisodicProxy | NoopEpisodic = field(default_factory=NoopEpisodic)

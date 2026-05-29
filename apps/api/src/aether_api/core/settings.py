"""Application settings — loaded from environment / `.env` via pydantic-settings.

Single source of truth for every tunable in the backend. Anything that varies
between environments (dev / test / prod) MUST flow through here; do not read
``os.environ`` directly from feature code.

Decisions (locked in design.md / orchestrator brief):

- Auth: custom FastAPI, NOT Auth.js / Clerk.
- JWT: HS256 with secret from ``JWT_SECRET`` env var. Min length 32 chars
  (≥ 256 bits of entropy when generated from /dev/urandom — anything
  shorter triggers a startup error rather than silently weakening security).
- Access token TTL = 15 min, refresh token TTL = 14 days.
- argon2id parameters: memory_cost=19_456, time_cost=2, parallelism=1
  (charter floor — never weaken).
- Failed-login lockout: 5 attempts → 15 min lockout.
- Cookies: ``Secure`` is OFF in dev (``cookie_secure=False``) and ON in prod.
- Signup is admin-only by default (``signup_open=False``).
- MFA: TOTP (RFC 6238). ``MFA_SECRET_KEY`` is the Fernet key that wraps
  ``users.mfa_secret_ref``; ``MFA_PENDING_SECRET`` signs the short-lived
  ``aether_mfa_pending`` cookie minted during the login two-step.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    AliasChoices,
    Field,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Frozen at process start; re-read via ``get_settings.cache_clear()`` in tests."""

    # ------------------------------------------------------------------
    # Environment / runtime mode
    # ------------------------------------------------------------------
    environment: Literal["dev", "test", "prod"] = "dev"

    # ------------------------------------------------------------------
    # Database — async URL (postgresql+asyncpg://...).
    # ------------------------------------------------------------------
    database_url: PostgresDsn

    # ------------------------------------------------------------------
    # JWT / tokens
    # ------------------------------------------------------------------
    # RS256 (asymmetric) is the default since the rs256-jwt-migration. HS256
    # remains available transitionally — see ``jwt_legacy_hs256_verify_enabled``
    # and the cleanup follow-up change.
    jwt_algorithm: Literal["RS256", "HS256"] = "RS256"

    # HS256 shared secret. Still required at boot because the transitional
    # HS256 verify-only fallback (below) needs something to verify against —
    # the cleanup change drops the field entirely. Min length is enforced
    # by the validator below.
    #     python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str

    # RS256 key material. EITHER the inline PEM (``JWT_PRIVATE_KEY_PEM``,
    # multi-line env or stringified via the SecretStr wrapper) OR a filesystem
    # path. Public key is published via the JWKS endpoint and MUST match the
    # private key; the loader (auth/keys.py) refuses to boot on a mismatch.
    jwt_private_key_pem: SecretStr | None = None
    jwt_private_key_path: Path | None = None
    jwt_public_key_pem: str | None = None
    jwt_public_key_path: Path | None = None

    # Transitional fallback: accept HS256-signed tokens for verification only
    # (never issuance). The rs256-jwt-cleanup follow-up flips this to False
    # and removes the fallback branch from verify_access_token entirely. Drains
    # within one access-token TTL (15 min) after issuance flips to RS256.
    jwt_legacy_hs256_verify_enabled: bool = True

    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # ------------------------------------------------------------------
    # Argon2id parameters — charter floor.
    # ------------------------------------------------------------------
    argon2_memory_cost: int = 19_456  # KiB → 19 MiB
    argon2_time_cost: int = 2
    argon2_parallelism: int = 1

    # ------------------------------------------------------------------
    # Brute-force throttling
    # ------------------------------------------------------------------
    lockout_threshold: int = 5
    lockout_window_minutes: int = 15

    # ------------------------------------------------------------------
    # Cookies
    # ------------------------------------------------------------------
    # ``Secure`` requires HTTPS — keep False in dev where the API runs on
    # http://localhost. The compose / prod overrides must set this True.
    cookie_secure: bool = False
    cookie_domain: str | None = None

    # ------------------------------------------------------------------
    # Signup / admin
    # ------------------------------------------------------------------
    # When False (v1 default), POST /api/auth/signup requires an admin
    # caller. When True, signup is open to the public — only enable this
    # behind a reverse proxy with rate limiting.
    signup_open: bool = False

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    # The Next.js dev server lives on :3000; production typically proxies
    # via the same origin so the list shrinks to []. Keep ``allow_credentials``
    # True downstream so cookies travel cross-origin during dev.
    cors_allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ------------------------------------------------------------------
    # Observability (every component is feature-flagged — unset = disabled)
    # ------------------------------------------------------------------
    #: Log level for the structlog root handler. ``INFO`` in prod, ``DEBUG``
    #: while chasing a regression. Anything else falls back to INFO at runtime.
    log_level: str = "INFO"

    #: Sentry DSN. Empty/unset means error reporting is OFF.
    sentry_dsn: str | None = None
    #: Backend release identifier passed to Sentry (e.g. ``api@0.0.1+sha``).
    sentry_release: str | None = None
    #: Environment tag forwarded to Sentry. Defaults to :attr:`environment`.
    sentry_environment: str | None = None

    #: OpenTelemetry OTLP exporter endpoint. Empty/unset means tracing is OFF.
    #: When set, the API initialises the OTel SDK + the FastAPI/SQLAlchemy
    #: instrumentors during the FastAPI lifespan startup.
    otel_exporter_otlp_endpoint: str | None = None
    #: Service-name tag for spans. Defaults to ``aether-api``.
    otel_service_name: str = "aether-api"

    #: When True, mount ``/metrics`` (admin-only) backed by
    #: ``prometheus-fastapi-instrumentator``. Disabled by default so a
    #: bootstrap env behaves identically to today's runtime.
    metrics_enabled: bool = False

    #: When True, the application writes append-only ``audit_log`` rows for
    #: state-changing endpoints. Disabled by default — the migration is
    #: applied but writes are gated until an operator opts in.
    audit_log_enabled: bool = False

    # ------------------------------------------------------------------
    # Per-project Docker orchestration (docker_control module)
    # ------------------------------------------------------------------
    #: HTTP endpoint of the tecnativa/docker-socket-proxy sidecar (see
    #: ``docker-compose.yml``). The API NEVER talks to ``/var/run/docker.sock``
    #: directly — every Docker API call flows through this proxy, which has
    #: a minimal allowlist (CONTAINERS, IMAGES, BUILD only). Anything else
    #: (EXEC, NETWORKS, VOLUMES, SWARM, INFO, AUTH) is denied at the proxy.
    #: A compromised API process therefore cannot pivot to host-root via
    #: the Docker socket — that's the central security invariant for the
    #: per-project Docker orchestration change.
    docker_host: str = "tcp://docker-proxy:2375"

    #: Period (seconds) of the background reconciliation sweep. The sweep
    #: walks every project with ``container_id IS NOT NULL`` and queries
    #: ``GET /containers/{id}/json`` on the proxy; on 404 the project is
    #: moved to ``error`` and ``container_id`` is cleared, with a row
    #: written to ``container_events`` for audit. Set to 0 to disable the
    #: ticker (the boot-time sweep still runs).
    docker_reconcile_interval_seconds: int = 30

    #: Default base image embedded in the generated Dockerfile when the
    #: project row does not override ``docker_image``. Matches the project
    #: model ``docker_image`` server default.
    docker_default_base_image: str = "mt5-base:latest"

    #: When True, the FastAPI lifespan starts the boot-time reconciliation
    #: sweep + the periodic ticker. Disabled by default so a bootstrap env
    #: (no Docker daemon, no proxy) keeps booting cleanly.
    docker_reconcile_enabled: bool = False

    # ------------------------------------------------------------------
    # MFA (TOTP, RFC 6238) — see services/secret_box.py + services/mfa.py.
    # ------------------------------------------------------------------
    #: Fernet key used by :class:`SecretBox` to wrap the per-user TOTP
    #: secret before it lands in ``users.mfa_secret_ref``. MUST be a 32-
    #: byte URL-safe base64 string (the Fernet key shape). Generate with::
    #:
    #:     python -c "import secrets,base64; \
    #:        print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    #:
    #: REQUIRED whenever any user has ``mfa_enabled=true``; the route
    #: handlers refuse to operate without it (decrypt fails closed).
    mfa_secret_key: SecretStr | None = None

    #: HS256 signing secret for the short-lived ``aether_mfa_pending``
    #: cookie minted during the login two-step. Path-scoped to
    #: ``/api/auth/login/mfa`` and lives 5 minutes; rotating this key
    #: invalidates any in-flight MFA challenges and is therefore safe.
    #: MUST be ≥ 32 chars.
    mfa_pending_secret: SecretStr | None = None

    #: TTL of the ``aether_mfa_pending`` cookie (seconds). 5 minutes is
    #: the spec-mandated upper bound — long enough for a hesitant user
    #: to fish their phone out of a pocket, short enough that a stolen
    #: pending cookie is operationally useless.
    mfa_pending_ttl_seconds: int = 5 * 60

    # ------------------------------------------------------------------
    # MT5 live trading — mt5-integration change.
    # ------------------------------------------------------------------
    #: Master kill-switch for live order placement. When False (v1
    #: default) ``POST /api/projects/{id}/orders`` returns 503 with
    #: ``{detail: {"code": "live_orders_disabled"}}``. Read-only
    #: endpoints (``/account``, ``/positions``, ``/history``, ``/candles``,
    #: ``GET /orders``) work regardless — they only depend on the per-
    #: project MCP being reachable. The migration and risk/audit code
    #: paths are always installed; only the final ``order_send`` call is
    #: gated.
    aether_live_orders_enabled: bool = False

    #: How long an approval row stays ``pending`` before the gate marks
    #: it ``expired``. 5 minutes default — long enough for an operator
    #: to alt-tab to the panel, short enough to bound risk if they walk
    #: away.
    aether_order_approval_ttl_seconds: int = 5 * 60

    # ------------------------------------------------------------------
    # Agent execution sandbox — feature flag + rlimit knobs.
    # See ``sdd/agent-execution-sandbox/{spec,design}``.
    # ------------------------------------------------------------------
    #: Master flag. When False (v1 default), ``POST /api/agents/{id}/run``
    #: returns 503 with ``{detail: "sandbox not enabled"}``. The migration
    #: and read endpoints stay available so the audit table is queryable
    #: even before the engine is opened up.
    agent_sandbox_enabled: bool = False

    #: Hard wall-clock deadline applied by the parent (SIGKILL on expiry).
    #: 15 s default — the spec's outermost defence layer.
    agent_sandbox_wall_clock_seconds: float = 15.0

    #: ``RLIMIT_CPU`` setting passed into the child. 10 s default.
    agent_sandbox_rlimit_cpu_seconds: int = 10

    #: ``RLIMIT_AS`` (address space) in bytes. Default 256 MiB.
    agent_sandbox_rlimit_as_bytes: int = 256 * 1024 * 1024

    #: ``RLIMIT_NOFILE``. Default 64 — enough for stdlib + numpy I/O
    #: scaffolding, far less than the parent's pool.
    agent_sandbox_rlimit_nofile: int = 64

    #: ``RLIMIT_FSIZE`` in bytes. Default 0 — disk writes forbidden.
    agent_sandbox_rlimit_fsize_bytes: int = 0

    # ------------------------------------------------------------------
    # MQL5 → Python translator — one-shot UX tool. See
    # ``sdd/mql5-to-py-translator``.
    #
    # The translator is NOT a runtime path: MQL5 input is never persisted,
    # never executed; only the resulting Python text is returned to the
    # caller (who may then save it as ``agents.logica``). Charter rule
    # "No MQL5 ever lands in the DB or runtime" is upheld here by
    # discarding the input after the upstream call returns.
    # ------------------------------------------------------------------
    #: Master flag. When False (v1 default) ``POST /api/tools/mql5-to-python``
    #: returns 503 with ``{detail: "translator not enabled"}``. Disabling at
    #: this layer skips the Anthropic call entirely — no API key required.
    mql5_translator_enabled: bool = False

    #: Anthropic API key. ``SecretStr`` so the structured-log PII scrubber
    #: masks it on accidental dumps. When the translator flag is True but
    #: this is unset, the endpoint returns 503 with
    #: ``{detail: "translator not configured"}``.
    anthropic_api_key: SecretStr | None = None

    #: Anthropic model id used for translation. Default is the current
    #: Claude Sonnet (May 2025); operators MAY override to a newer
    #: model — the name will drift over time and is intentionally a
    #: simple env knob, not a code change.
    mql5_translator_model: str = "claude-sonnet-4-20250514"

    #: Hard cap on the inbound MQL5 source (bytes). 50 KiB is already
    #: generous for an EA — anything larger almost certainly indicates
    #: bundled libraries or an attempt to abuse the translator as a
    #: general code-generation channel. Enforced BEFORE the upstream
    #: call so the API key isn't burned on oversize jobs.
    mql5_translator_max_input_bytes: int = 50 * 1024

    # ------------------------------------------------------------------
    # Sleep Phase — feature flag + scheduler knobs.
    # See ``sdd/sleep-phase/{spec,design}``.
    # ------------------------------------------------------------------
    #: Master flag. When False (v1 default), the APScheduler does NOT
    #: auto-fire Micro / Profundo jobs. Manual triggers
    #: (POST /api/projects/{id}/sleep/trigger) still work — that's the
    #: operator escape hatch for debugging the workflow without enabling
    #: the recurring schedule. Crítico (auditor-event) bypasses the
    #: scheduler entirely; see orchestrator.py.
    sleep_scheduler_enabled: bool = False

    #: Default Micro interval (hours) per project. Charter window is 4-8.
    #: Per-project overrides can come from project rows in a future iteration;
    #: for v1 every project uses this single value.
    sleep_micro_default_hours: int = 6

    #: Cron expression for the daily Sueño Profundo. Default 00:00 UTC.
    #: Five-field standard cron (m h dom mon dow).
    sleep_profundo_cron: str = "0 0 * * *"

    #: Window (hours) during which a reverted snapshot may be re-applied.
    #: Used by the revert endpoint to refuse revert requests on snapshots
    #: older than this. v1 ships 72h; operators may relax in prod.
    sleep_revert_window_hours: int = 72

    #: Boot sweep predicate — runs older than this many minutes still in
    #: 'running' status are marked 'crashed' at app startup. Long enough
    #: to cover the wall-clock cap of the longest reflection (15s × 3
    #: agents + synthesis) with a generous safety factor.
    sleep_stale_run_minutes: int = 30

    # ------------------------------------------------------------------
    # Sleep-learning loop — Q-Table classifier knob.
    # See ``sdd/sleep-learning-loop`` (design #2070, classifier section).
    # ------------------------------------------------------------------
    #: Number of most-frequent ``state_key`` rows the Q-Table classifier
    #: walks per project before falling back to the magnitude bracket.
    #: 50 is the design default — wide enough to cover the heavy tail of
    #: a v1 project's episodic memory, narrow enough that the walk stays
    #: O(1) per Sleep Phase run regardless of long-tail state cardinality.
    learning_classifier_topk: int = 50

    #: Master flag for the sleep-learning loop (Phase 11 of
    #: ``sdd/sleep-learning-loop``). When False (v1 default):
    #:
    #: * The Sleep Phase orchestrator skips Step 5a/5b/5c — the deep-sleep
    #:   path falls back to the legacy ``cv_repo.create`` write.
    #: * The sandbox child binds the inert ``NoopQTable`` / ``NoopSemantic``
    #:   / ``NoopEpisodic`` proxies on ``ctx`` — agent code that calls them
    #:   gets the documented no-op behaviour, never a DB write.
    #: * The lifespan ``warm_caches`` pass is skipped — projects start cold
    #:   and the cache stays empty.
    #:
    #: Reads from the legacy ``AETHER_LEARNING_ENABLED`` env var first
    #: (this is the historical knob Phase 7 shipped with) and falls back
    #: to the shorter ``LEARNING_ENABLED`` for symmetry with the other
    #: feature flags. Accepts the standard pydantic booleans
    #: (``true`` / ``1`` / ``yes`` / ``on`` plus their falsy counterparts),
    #: case-insensitive.
    learning_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "AETHER_LEARNING_ENABLED",
            "LEARNING_ENABLED",
        ),
    )

    #: Soft-warn threshold (bytes) for a freshly-persisted ``q_tables`` row.
    #: When the JSON payload of a Q-Table version exceeds this many bytes,
    #: the learning metrics path emits a structured WARN log line
    #: (``aether.qtable.size_threshold_breached``) carrying project_id +
    #: bytes. Default is 50 MiB — the design budget for a long-tail v1
    #: project. Tests override to a much smaller value so the threshold
    #: can be exercised without piping megabytes through a fake.
    learning_qtable_warn_bytes: int = 50 * 1024 * 1024

    #: Token-bucket capacity for the cross-tenant write audit log. Each
    #: distinct ``actor_user_id`` gets a bucket of this many warns per
    #: refill window; over-limit attempts are still rejected by the
    #: repository (``PermissionError``) but drop their structured log
    #: line to keep stdout from being weaponised by a hostile caller.
    learning_audit_rate_capacity: int = 10

    #: Refill window (seconds) for the cross-tenant audit token bucket.
    #: One token is added every ``capacity / window`` seconds; a fresh
    #: actor starts with a full bucket. Default 60 s — short enough to
    #: catch a sustained attack, long enough that legitimate operator
    #: tooling never trips it.
    learning_audit_rate_window_seconds: float = 60.0

    # ------------------------------------------------------------------
    # Operativa write proxy — sandbox-side OrdersProxy injection.
    # See ``sdd/project-operativa/spec/agent-sandbox-delta`` (#2119).
    # ------------------------------------------------------------------
    #: Master flag for the Operativa write proxy. When True (v1 default),
    #: every sandboxed Worker invocation receives a ``ctx.orders``
    #: :class:`OrdersProxy` whose three methods (``record_open`` /
    #: ``record_modify`` / ``record_close``) route through the existing
    #: parent ↔ child RPC pipe to the ``orders`` table. When False, the
    #: child binds the inert ``NoopOrders`` variant — any method raises
    #: ``RuntimeError("operativa proxy disabled")`` so a Worker that
    #: depends on the proxy fails loudly rather than silently dropping
    #: writes. Mirrors the ``learning_enabled`` pattern from
    #: ``sleep-learning-loop``: env-name alias kept stable so deployments
    #: can toggle with ``AETHER_OPERATIVA_PROXY_ENABLED``.
    operativa_proxy_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AETHER_OPERATIVA_PROXY_ENABLED",
            "OPERATIVA_PROXY_ENABLED",
        ),
    )

    # ------------------------------------------------------------------
    # Operativa WebSocket + LiveBus — push surface for the operator
    # Operativa tab. See ``sdd/project-operativa/spec/operativa-live``
    # (#2123) and ``sdd/project-operativa/spec/multi-tenancy-delta``
    # (#2122). When False the WS router is NOT mounted and the LiveBus
    # background tasks are NOT started; the REST surface continues to
    # work (degraded UX, but the frontend polls in that mode).
    # ------------------------------------------------------------------
    operativa_ws_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AETHER_OPERATIVA_WS_ENABLED",
            "OPERATIVA_WS_ENABLED",
        ),
    )

    # ------------------------------------------------------------------
    # pydantic-settings config
    # ------------------------------------------------------------------
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Tolerate unrelated vars (e.g. POSTGRES_PASSWORD, NEXT_PUBLIC_*) without
        # crashing — pydantic-settings is strict-by-default which is wrong here.
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_strong_enough(cls, v: str) -> str:
        """Refuse to boot with a short JWT secret.

        32 chars of base64-ish entropy ≈ 24 random bytes ≈ 192 bits — enough
        for HS256 in practice. Anything shorter is almost certainly a
        placeholder left over from a tutorial.
        """
        if len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters long. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return v

    @field_validator("mfa_secret_key")
    @classmethod
    def _mfa_secret_key_is_fernet_shaped(cls, v: SecretStr | None) -> SecretStr | None:
        """Refuse a malformed Fernet key at boot.

        Fernet keys are exactly 32 random bytes encoded URL-safe-base64 →
        44 characters (with the trailing ``=``). We accept both shapes
        callers may produce — with or without the trailing ``=`` — but
        reject anything that doesn't decode to 32 bytes. A wrong-length
        key would otherwise blow up only on the first ``encrypt`` call,
        which is too late to be useful in CI.
        """
        if v is None:
            return v
        raw = v.get_secret_value()
        # Pad to length-mod-4 so callers who dropped the trailing ``=``
        # (a common copy/paste artifact) still validate.
        padded = raw + "=" * (-len(raw) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (ValueError, binascii.Error) as exc:
            raise ValueError(
                "MFA_SECRET_KEY must be URL-safe base64 (Fernet shape). "
                "Generate one with: python -c \"import secrets,base64;"
                "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\""
            ) from exc
        if len(decoded) != 32:
            raise ValueError(
                "MFA_SECRET_KEY must decode to exactly 32 bytes "
                f"(got {len(decoded)})."
            )
        return v

    @field_validator("mfa_pending_secret")
    @classmethod
    def _mfa_pending_secret_strong_enough(cls, v: SecretStr | None) -> SecretStr | None:
        """Refuse a short HS256 secret for the pending cookie."""
        if v is None:
            return v
        if len(v.get_secret_value()) < 32:
            raise ValueError(
                "MFA_PENDING_SECRET must be at least 32 characters long. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @model_validator(mode="after")
    def _rs256_keys_required(self) -> Settings:
        """When ``jwt_algorithm`` is RS256, both private + public key sources MUST be set.

        Either ``..._pem`` (inline) OR ``..._path`` (filesystem) for each side.
        We fail fast here so a misconfigured deployment doesn't silently fall
        through to HS256 (or, worse, NoneType-blow up deep inside the signer).
        """
        if self.jwt_algorithm != "RS256":
            return self
        if self.jwt_private_key_pem is None and self.jwt_private_key_path is None:
            raise ValueError(
                "JWT_ALGORITHM=RS256 requires JWT_PRIVATE_KEY_PEM or "
                "JWT_PRIVATE_KEY_PATH. Generate dev keys with: "
                "uv run python apps/api/scripts/gen_dev_keys.py"
            )
        if self.jwt_public_key_pem is None and self.jwt_public_key_path is None:
            raise ValueError(
                "JWT_ALGORITHM=RS256 requires JWT_PUBLIC_KEY_PEM or JWT_PUBLIC_KEY_PATH."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance.

    The cache means importers can call this freely without paying the
    ``.env`` parse cost more than once. Tests that need to mutate the
    environment should call ``get_settings.cache_clear()`` after rewriting
    ``os.environ``.
    """
    return Settings()  # type: ignore[call-arg]  # values come from env / .env

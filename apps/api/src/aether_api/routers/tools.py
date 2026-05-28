"""``/api/tools/*`` — one-shot helper endpoints that don't fit elsewhere.

Currently a single surface: ``POST /api/tools/mql5-to-python`` —
translates MQL5 source into Python that uses the project's MCP wrapper.

Charter alignment
-----------------
* **No MQL5 ever lands in the database or runtime.** The endpoint
  consumes MQL5 in the request body, returns Python in the response,
  and discards the input the moment the upstream call returns.
* The audit log row records SIZES only (``mql5_size`` /
  ``python_size``) — never the actual content of either side. This
  guards operator privacy and avoids the audit table becoming a
  back-channel for prompt content.
* The Anthropic API key never appears in any log: it's a
  ``SecretStr`` in settings, the structlog PII scrubber masks it on
  accidental dumps, and we never echo it in errors.

Gates (evaluated in order — the first one that fails short-circuits):

1. **Auth** — any authenticated user. CSRF required (state-changing).
2. **Feature flag** — :attr:`Settings.mql5_translator_enabled` must be
   True; otherwise 503 ``{detail: "translator not enabled"}``.
3. **Body size** — input MQL5 must be ≤
   :attr:`Settings.mql5_translator_max_input_bytes`; otherwise 413.
4. **API key** — :attr:`Settings.anthropic_api_key` must be set;
   otherwise 503 ``{detail: "translator not configured"}``.
5. **Upstream** — Anthropic errors collapse into a stable 502 with
   ``{code: "translator_upstream_error"}``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aether_api.core.settings import get_settings
from aether_api.db.session import get_session
from aether_api.models.user import User
from aether_api.repositories.audit_repository import AuditRepository
from aether_api.services.mql5_translator import (
    TranslatorNotConfiguredError,
    TranslatorUpstreamError,
    translate_mql5,
)
from aether_api.tenancy.middleware import csrf_dependency, current_user

router = APIRouter(prefix="/api/tools", tags=["tools"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class Mql5ToPythonRequest(BaseModel):
    """Body of ``POST /api/tools/mql5-to-python``.

    ``target_entrypoint`` mirrors ``agents.entrypoint``; the translator
    bakes the requested name into the generated Python so the operator
    can paste straight into the agent without renaming. Defaults to
    ``on_tick`` (Worker convention).
    """

    model_config = ConfigDict(extra="forbid")

    mql5: Annotated[str, Field(min_length=1)]
    target_entrypoint: Annotated[
        str,
        Field(
            default="on_tick",
            min_length=1,
            max_length=120,
            pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,119}$",
        ),
    ]


class Mql5ToPythonResponse(BaseModel):
    """Successful translation envelope.

    ``python`` is the Python source the editor displays in the right
    pane of the modal. Token accounting fields are exposed so the UI
    can show the operator how expensive the translation was (and so
    the audit log size accounting has the same numbers).
    """

    python: str
    model: str
    input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/mql5-to-python",
    response_model=Mql5ToPythonResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(csrf_dependency)],
)
async def mql5_to_python(
    payload: Mql5ToPythonRequest,
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Mql5ToPythonResponse:
    """Translate MQL5 to Python via Anthropic. One-shot, stateless."""
    settings = get_settings()

    # Gate 1 — feature flag. Short-circuits BEFORE we touch the body
    # so a misconfigured cluster never accidentally accepts uploads.
    if not settings.mql5_translator_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="translator not enabled",
        )

    # Gate 2 — body size. ``len(payload.mql5.encode())`` gives the
    # wire-level byte count, which is what the cap is denominated in.
    mql5_size = len(payload.mql5.encode("utf-8"))
    if mql5_size > settings.mql5_translator_max_input_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "mql5_too_large",
                "size_bytes": mql5_size,
                "max_bytes": settings.mql5_translator_max_input_bytes,
            },
        )

    # Gate 3 + upstream. ``translate_mql5`` raises typed errors which
    # we translate into stable HTTP codes; the SDK exception class is
    # NEVER leaked into the response.
    try:
        result = translate_mql5(
            mql5=payload.mql5,
            target_entrypoint=payload.target_entrypoint,
        )
    except TranslatorNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="translator not configured",
        ) from exc
    except TranslatorUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "translator_upstream_error",
                "message": "the translation upstream returned an error",
            },
        ) from exc

    # Audit log — SIZE only. We intentionally do NOT persist the MQL5
    # input or the Python output. The size pair plus the model id is
    # enough for ops to spot abuse (oversize inputs, runaway outputs)
    # without making the audit table a leak surface.
    python_size = len(result.python.encode("utf-8"))
    audit = AuditRepository(session)
    await audit.record(
        user_id=user.id,
        action="mql5_translate",
        target_type="tools",
        target_id=None,
        before={
            "mql5_size": mql5_size,
            "target_entrypoint": payload.target_entrypoint,
        },
        after={
            "python_size": python_size,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        request=request,
    )
    await session.commit()

    return Mql5ToPythonResponse(
        python=result.python,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )

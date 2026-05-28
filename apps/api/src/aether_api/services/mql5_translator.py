"""One-shot MQL5 → Python translator backed by the Anthropic API.

Charter context
---------------
Aether's hard rule (CLAUDE.md / CHARTER.md): **no MQL5 ever lands in the
database or runtime**. The translator is purely a UX convenience — paste
MQL5 in, get Python back, drop it into the agent's ``logica`` editor for
human review. The input is discarded the moment this function returns;
the output is plain Python that talks to MT5 only through the project's
MCP wrapper (``ctx.mcp.*``), never via direct broker calls.

Surface
-------
``translate_mql5(mql5, target_entrypoint) -> TranslationResult``

This module deliberately exposes a single pure-ish function (network call
to Anthropic notwithstanding). Wiring — feature-flag gating, body-size
caps, audit logging, HTTP shape — lives in ``routers/tools.py``.

Errors
------
The function raises:

* :class:`TranslatorNotConfiguredError` — API key missing on Settings.
* :class:`TranslatorUpstreamError` — anything the Anthropic SDK throws
  (network, auth, rate limit, response shape).

The endpoint translates both into stable HTTP status codes; this module
does NOT raise HTTPException so it stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aether_api.core.settings import get_settings

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslationResult:
    """Carrier for a successful Anthropic round-trip.

    The endpoint relays ``python`` to the operator and uses the
    accounting fields (``model``, ``input_tokens``, ``output_tokens``)
    for audit-log size accounting and operator visibility. Neither the
    MQL5 input nor the Python output is stored alongside these counts.
    """

    python: str
    model: str
    input_tokens: int
    output_tokens: int


class TranslatorError(Exception):
    """Base exception for translator-side failures."""


class TranslatorNotConfiguredError(TranslatorError):
    """Raised when the feature is flagged on but ``ANTHROPIC_API_KEY`` is missing."""


class TranslatorUpstreamError(TranslatorError):
    """Raised when the Anthropic SDK throws or returns an unexpected shape."""


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
#
# Pinned in code (not a settings field) because changes to this prompt
# must go through code review — drift here is the difference between
# safe output and "Python that calls MetaTrader5.send() directly". The
# prompt enforces three invariants:
#
#   1. Output is Python only — no markdown fences, no chatter.
#   2. Order placement funnels through ``ctx.mcp.place_order(...)`` with
#      a mandatory SL — never ``MetaTrader5.*`` direct calls.
#   3. The translation is annotated as auto-generated with a TODO so a
#      reviewer is reminded to audit it before saving.
#
# Mapping hints below are intentionally short. The model is good enough
# to pick up idioms (``OnTick`` → ``on_tick``, ``OrderSend`` →
# ``ctx.mcp.place_order``, ``iRSI`` → a skill/indicator call); we
# anchor the contract and let it do the work.

_SYSTEM_PROMPT: Final[
    str
] = """You translate MetaTrader 4/5 MQL5 source code into Python that runs \
inside Aether Trading System's agent runtime.

HARD CONSTRAINTS — never violate:

1. OUTPUT IS PYTHON ONLY. No markdown fences (no ``` ``` blocks), no \
prose, no chat. The very first character of your reply MUST be a Python \
source character (typically ``#`` from the auto-generation header). The \
very last character MUST be a Python source character (typically a \
newline). Anything else breaks the calling code.

2. NEVER emit ``import MetaTrader5`` or any direct ``mt5.*`` / \
``MetaTrader5.*`` call. Orders, account info, candles, positions — \
EVERYTHING goes through the Aether MCP wrapper exposed on the agent \
context object ``ctx``:
   * Orders:        ``ctx.mcp.place_order(symbol, side, volume, sl=..., tp=...)``
   * Positions:     ``ctx.mcp.get_positions()``
   * Account:       ``ctx.mcp.get_account()``
   * Candles/quotes:``ctx.mcp.get_candles(symbol, timeframe, count)``
   * History:       ``ctx.mcp.get_history(...)``
   Mandatory Stop-Loss on every order — never omit ``sl=``. If the \
   source MQL5 sends an order without SL, add ``sl=ctx.params['default_sl']`` \
   and leave a ``# TODO`` comment noting the missing SL was inferred.

3. Top of file MUST be exactly this header (one ``# TODO`` block):
       # TODO: review — auto-translated from MQL5.
       # Re-check entrypoint, risk parameters, and MCP tool names before saving.

4. Map MQL5 idioms to Aether conventions:
   * ``OnTick()``      → ``def {entrypoint}(ctx):`` (entrypoint name \
provided per call, default ``on_tick``)
   * ``OnInit()``      → optional ``def on_init(ctx):`` module-level
   * ``OnDeinit()``    → optional ``def on_deinit(ctx):`` module-level
   * ``OrderSend(...)``→ ``ctx.mcp.place_order(...)`` with explicit ``sl=``
   * Indicators (``iRSI``, ``iMA``, ``iATR``, ...) → call the matching \
     Aether skill via ``ctx.skills['<skill_name>'](...)`` if obvious; \
     otherwise compute with ``numpy`` / ``pandas_ta`` and leave a \
     ``# TODO`` comment.
   * MQL5 globals (``Bid``, ``Ask``, ``Symbol()``, ``Period()``) → read \
     from ``ctx.symbol``, ``ctx.timeframe``, ``ctx.tick`` (latest tick).
   * ``Print(...)``    → ``ctx.log.info(...)``
   * ``Alert(...)``    → ``ctx.log.warning(...)``

5. Preserve every meaningful comment from the original. Translate \
Spanish/English comments verbatim; do not invent new ones beyond the \
TODO markers above.

6. If the input is empty, unparseable, or clearly not MQL5, return a \
Python stub with the auto-generation header, a ``def {entrypoint}(ctx):`` \
function body containing ``raise NotImplementedError(...)``, and a \
``# TODO`` line explaining what was unparseable. NEVER refuse with prose.

Remember: you are a code translator, not an assistant. Your entire \
response is consumed as Python source.
"""


# ---------------------------------------------------------------------------
# translate_mql5 — the one public entry point
# ---------------------------------------------------------------------------


def translate_mql5(
    mql5: str,
    target_entrypoint: str = "on_tick",
) -> TranslationResult:
    """Translate an MQL5 snippet to Python via the Anthropic Messages API.

    Parameters
    ----------
    mql5:
        Raw MQL5 source. Size limits / feature-flag gating are NOT
        enforced here — that's the router's job. We trust the caller.
    target_entrypoint:
        Function name the operator wants the Python entrypoint to be
        called (matches ``agents.entrypoint``). Defaults to ``on_tick``
        (Worker convention).

    Returns
    -------
    :class:`TranslationResult`
        Carrier with the Python source and token-accounting fields. The
        MQL5 input is NOT echoed back.

    Raises
    ------
    TranslatorNotConfiguredError
        When ``settings.anthropic_api_key`` is unset.
    TranslatorUpstreamError
        On any SDK exception or unexpected response shape.
    """
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise TranslatorNotConfiguredError("ANTHROPIC_API_KEY is not configured")

    # Lazy import — keeps the cold-start cost off the FastAPI process
    # when the feature is disabled, and lets the test suite stub the
    # SDK without dragging in real HTTP.
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover — covered by pyproject dep
        raise TranslatorUpstreamError(
            "anthropic SDK not installed; check pyproject dependencies"
        ) from exc

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value()
    )

    # The user message frames the task: which entrypoint name the
    # operator expects, then the raw MQL5. We do NOT pretty-print or
    # otherwise alter the source — the model handles formatting.
    user_message = (
        f"Target Python entrypoint: {target_entrypoint}\n"
        f"---\n"
        f"{mql5}"
    )

    try:
        response = client.messages.create(
            model=settings.mql5_translator_model,
            max_tokens=8192,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:  # noqa: BLE001 — opaque upstream surface
        raise TranslatorUpstreamError(
            f"anthropic API call failed: {type(exc).__name__}"
        ) from exc

    python_text = _extract_text_from_response(response)
    # Defensive scrub: if the model still wrapped the output in fences
    # despite the prompt, peel them. We never want a markdown fence to
    # land in ``agents.logica``.
    python_text = _strip_code_fences(python_text)

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    return TranslationResult(
        python=python_text,
        model=settings.mql5_translator_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_text_from_response(response: object) -> str:
    """Pull the joined text blocks out of an Anthropic ``Message`` response.

    The SDK returns a list of content blocks; we concatenate every
    ``TextBlock.text``. The shape is defensive — both ``.content`` and
    ``.text`` are accessed via ``getattr`` so a hypothetical SDK upgrade
    that changes the block class name doesn't cause an AttributeError
    deep in the request path; instead we raise our own typed error.
    """
    content = getattr(response, "content", None)
    if not content:
        raise TranslatorUpstreamError("anthropic response had no content blocks")

    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    if not parts:
        raise TranslatorUpstreamError(
            "anthropic response had no text in any content block"
        )
    return "".join(parts)


def _strip_code_fences(text: str) -> str:
    """Peel a leading/trailing ```` ``` ```` fence if the model added one.

    The prompt forbids fences, but we still strip defensively — a
    stray fence inside ``agents.logica`` would make the Python file
    refuse to parse.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    # Drop the first line (``` or ```python ...).
    first_newline = stripped.find("\n")
    if first_newline == -1:
        # Entire reply was one fence — nothing usable; surface as upstream
        # error so the operator sees a clear failure rather than empty code.
        raise TranslatorUpstreamError(
            "anthropic response contained only a code fence with no body"
        )
    body = stripped[first_newline + 1 :]
    if body.endswith("```"):
        body = body[: -3]
    return body.rstrip() + "\n"

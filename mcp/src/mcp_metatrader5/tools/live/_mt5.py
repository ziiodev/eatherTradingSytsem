"""Thin wrapper around the ``MetaTrader5`` Python package.

Why a wrapper:

* The official binding is Windows-only at import time. On Linux/CI we
  must be able to *import* the live tool modules without blowing up; we
  only blow up when the tool is actually called. The wrapper achieves
  that with a lazy import + a guard that surfaces a typed
  :class:`MT5Error` instead of an ``ImportError``.
* The official binding's connect path (``initialize`` + ``login``) is
  effectful and not thread-safe; we serialise via a module-level
  :class:`threading.Lock` and only ``initialize`` once per process.
* The wrapper centralises credential redaction at the log boundary —
  callers never have to remember to ``redact()`` themselves.

The wrapper exposes a single :class:`MT5Bridge` instance via
:func:`get_bridge`. Tests substitute their own ``MetaTrader5``-shaped
fake by patching the module-level ``_mt5_module`` attribute (see
``tests/unit/test_live_*.py`` once written).
"""

from __future__ import annotations

import threading
from typing import Any

from ...config import Settings
from ...errors import ErrorCode, MT5Error
from ...logging import get_logger

_log = get_logger(__name__)

# Module-level state.
_lock: threading.Lock = threading.Lock()
_initialized: bool = False
#: Reference to the ``MetaTrader5`` module — lazily imported. Tests patch
#: this with a stub. ``None`` until first :meth:`MT5Bridge.ensure_ready`.
_mt5_module: Any | None = None


def _import_mt5() -> Any:
    """Import ``MetaTrader5`` lazily and surface a typed error on failure."""
    global _mt5_module
    if _mt5_module is not None:
        return _mt5_module
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise MT5Error(
            ErrorCode.MT5_NOT_INITIALIZED,
            "MetaTrader5 Python package is not installed on this platform. "
            "The live trading tools require the official binding "
            "(Windows-native or in-Wine).",
        ) from exc
    _mt5_module = mt5
    return mt5


def _last_error_code() -> int | None:
    """Return MT5's ``last_error()`` integer code, ignoring the message."""
    try:
        mt5 = _import_mt5()
        result = mt5.last_error()
    except MT5Error:
        return None
    if isinstance(result, tuple) and result:
        first = result[0]
        if isinstance(first, int):
            return first
    if isinstance(result, int):
        return result
    return None


def _redact_login(login: int | None) -> str:
    """Render the MT5 login so only the last 4 digits hit the log stream."""
    if login is None:
        return "<unset>"
    s = str(login)
    if len(s) <= 4:
        return "***"
    return f"***{s[-4:]}"


class MT5Bridge:
    """Single-instance wrapper around the ``MetaTrader5`` package.

    Holds the gate that every live tool flows through
    (:meth:`ensure_ready`) plus credential redaction at the log
    boundary.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------ gate
    def ensure_ready(self) -> Any:
        """Return the imported ``MetaTrader5`` module, or raise typed.

        Order of guards:

        1. ``settings.live_enabled`` — flipped by the deployer. When
           False, refuse with :class:`ErrorCode.LIVE_DISABLED` regardless
           of platform / binding availability.
        2. Lazy import of the binding (:func:`_import_mt5`).
        3. One-shot ``initialize()`` + optional ``login()`` behind the
           module lock.
        """
        if not self.settings.live_enabled:
            raise MT5Error(
                ErrorCode.LIVE_DISABLED,
                "Live MT5 tools are disabled. Set MT5_LIVE_ENABLED=true to "
                "permit live connectivity.",
            )

        mt5 = _import_mt5()

        global _initialized
        if _initialized:
            return mt5

        with _lock:
            if _initialized:
                return mt5

            init_args: dict[str, Any] = {}
            if self.settings.terminal_path is not None:
                init_args["path"] = str(self.settings.terminal_path)

            try:
                ok = mt5.initialize(**init_args)
            except Exception as exc:  # pragma: no cover — defensive
                raise MT5Error(
                    ErrorCode.MT5_CONNECT_FAILED,
                    f"mt5.initialize() raised: {exc}",
                    mt5_retcode=_last_error_code(),
                ) from exc
            if not ok:
                code = _last_error_code()
                raise MT5Error(
                    ErrorCode.MT5_CONNECT_FAILED,
                    "mt5.initialize() returned False",
                    mt5_retcode=code,
                )

            if self.settings.mt5_login is not None:
                try:
                    logged_in = mt5.login(
                        login=int(self.settings.mt5_login),
                        password=self.settings.mt5_password or "",
                        server=self.settings.mt5_server or "",
                    )
                except Exception as exc:  # pragma: no cover — defensive
                    raise MT5Error(
                        ErrorCode.MT5_AUTH_FAILED,
                        f"mt5.login() raised: {exc}",
                        mt5_retcode=_last_error_code(),
                    ) from exc
                if not logged_in:
                    raise MT5Error(
                        ErrorCode.MT5_AUTH_FAILED,
                        "mt5.login() returned False",
                        mt5_retcode=_last_error_code(),
                    )

                _log.info(
                    "mt5_login_ok",
                    login=_redact_login(self.settings.mt5_login),
                    server=self.settings.mt5_server,
                )

            _initialized = True
            return mt5


def get_bridge(settings: Settings) -> MT5Bridge:
    """Return a :class:`MT5Bridge` — currently a fresh instance per call.

    Process-wide state lives in module globals (``_initialized``, the
    module-level lock, ``_mt5_module``) so caller cardinality does not
    matter. A pooled instance helps no-one here.
    """
    return MT5Bridge(settings=settings)


def _reset_for_tests() -> None:
    """Reset the module's global state. **Tests only.**"""
    global _initialized, _mt5_module
    with _lock:
        _initialized = False
        _mt5_module = None


__all__ = ["MT5Bridge", "get_bridge"]

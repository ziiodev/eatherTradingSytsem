"""Unit tests for the env-var config loader."""

from __future__ import annotations

import pytest

from mcp_metatrader5.config import load_settings
from mcp_metatrader5.errors import ConfigError, ErrorCode


def _base_env(tmp_path) -> dict[str, str]:
    return {
        "MT5_WINEPREFIX": str(tmp_path / "wine"),
        "MT5_TERMINAL_PATH": "C:/Program Files/MetaTrader 5/terminal64.exe",
        "MT5_METAEDITOR_PATH": "C:/Program Files/MetaTrader 5/metaeditor64.exe",
    }


def test_load_settings_minimal(tmp_path) -> None:
    env = _base_env(tmp_path)
    settings = load_settings(env)
    assert settings.wineprefix.is_absolute()
    assert settings.terminal_path.name == "terminal64.exe"
    assert settings.metaeditor_path.name == "metaeditor64.exe"
    assert settings.workspace_dir.is_absolute()
    assert settings.log_level == "INFO"
    assert settings.log_json is True
    assert settings.run_timeout_seconds == 3600
    assert settings.compile_timeout_seconds == 120
    assert settings.use_xvfb is True


def test_load_settings_no_wine_is_ok(tmp_path) -> None:
    """Empty env now yields a Wine-less Settings (server runs without compile/backtest)."""
    settings = load_settings({})
    assert settings.wine_available is False
    assert settings.wineprefix is None
    assert settings.terminal_path is None
    assert settings.metaeditor_path is None


def test_load_settings_partial_wine_rejected(tmp_path) -> None:
    """If MT5_TERMINAL_PATH is set but MT5_WINEPREFIX is not, that's a config error."""
    with pytest.raises(ConfigError) as info:
        load_settings({"MT5_TERMINAL_PATH": "/path/to/terminal64.exe"})
    assert info.value.code is ErrorCode.CONFIG_MISSING


def test_load_settings_full_wine(tmp_path) -> None:
    settings = load_settings(_base_env(tmp_path))
    assert settings.wine_available is True


def test_load_settings_unknown_var(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_UNKNOWN_OPTION"] = "x"
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert info.value.code is ErrorCode.CONFIG_INVALID
    assert "MT5_UNKNOWN_OPTION" in info.value.message


def test_load_settings_bool_coercion(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_LOG_JSON"] = "false"
    env["MT5_XVFB"] = "0"
    settings = load_settings(env)
    assert settings.log_json is False
    assert settings.use_xvfb is False


def test_load_settings_bool_invalid(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_LOG_JSON"] = "maybe"
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    assert info.value.code is ErrorCode.CONFIG_INVALID


def test_load_settings_int_coercion(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_RUN_TIMEOUT_SECONDS"] = "60"
    env["MT5_COMPILE_TIMEOUT_SECONDS"] = "30"
    settings = load_settings(env)
    assert settings.run_timeout_seconds == 60
    assert settings.compile_timeout_seconds == 30


def test_load_settings_int_invalid(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_RUN_TIMEOUT_SECONDS"] = "not-a-number"
    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_log_level_invalid(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_LOG_LEVEL"] = "VERBOSE"
    with pytest.raises(ConfigError):
        load_settings(env)


def test_load_settings_workspace_override(tmp_path) -> None:
    env = _base_env(tmp_path)
    env["MT5_WORKSPACE_DIR"] = str(tmp_path / "ws")
    settings = load_settings(env)
    assert settings.workspace_dir == (tmp_path / "ws").resolve()

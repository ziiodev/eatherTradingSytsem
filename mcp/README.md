# mcp-metatrader5

An MCP server that bridges Claude Code (or any MCP client) to a Wine-hosted
MetaTrader 5 install on Linux. Exposes tools for registering Expert Advisors,
compiling them with MetaEditor, and running backtests / optimizations via
the MT5 terminal Strategy Tester.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Foundation (pyproject, config, errors, logging, state, workspace) | Done |
| 2 | INI builder + log/report/XML parsers | Done |
| 3 | Wine subprocess runner + headless smoke test | **Pending** (no real MT5/Wine driver yet) |
| 4 | Orchestration (`RunManager` + `WineRunnerProtocol`) + tool schemas | Done |
| 5 | Tool functions (register/list/get/remove EAs, compile, backtest, optimize, runs) | Done |
| 6 | MCP server entrypoint + e2e fake-runner test + integration scaffold | Done |
| 7 | Docs + client config | Done |

The server is launchable today even without Wine. Read-only tools
(`mt5_list_eas`, `mt5_get_ea`, `mt5_list_runs`, `mt5_get_run`,
`mt5_get_run_artifact`) and `mt5_register_ea` / `mt5_remove_ea` work end-to-end.
`mt5_compile_ea`, `mt5_backtest_ea`, and `mt5_optimize_ea` enqueue jobs that
will fail with `WINE_RUNNER_NOT_IMPLEMENTED` until Phase 3 lands.

## Install

```bash
cd mcp
uv sync --all-extras
```

Verify:

```bash
uv run pytest -q          # 159 unit + e2e tests pass; 2 integration tests skip
uv run ruff check         # clean
uv run mypy               # strict, clean
```

## Run

```bash
uv run mcp-metatrader5             # speaks MCP over stdio
uv run mcp-metatrader5 --help      # env var reference
```

The server speaks the standard MCP JSON-RPC protocol over stdio and is
designed to be launched by a client (Claude Desktop, the Claude Code CLI,
any other MCP-aware host) — not to be invoked directly by a human.

## Configuration (env vars)

Every variable is optional unless marked otherwise. Unknown `MT5_*` variables
are rejected at startup so typos don't silently fall back to defaults.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MT5_WORKSPACE_DIR` | no | `./workspace` | SQLite state + per-run artifact directory |
| `MT5_WINEPREFIX` | conditional | — | Wine prefix that hosts MT5; required iff any MT5 path is set |
| `MT5_TERMINAL_PATH` | conditional | — | Absolute path to `terminal64.exe` |
| `MT5_METAEDITOR_PATH` | conditional | — | Absolute path to `metaeditor64.exe` |
| `MT5_LOG_LEVEL` | no | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `MT5_LOG_JSON` | no | `true` | If `false`, emit colourless console logs to stderr |
| `MT5_RUN_TIMEOUT_SECONDS` | no | `3600` | Per-run timeout (backtest/optimize) |
| `MT5_COMPILE_TIMEOUT_SECONDS` | no | `120` | Per-compile timeout |
| `MT5_XVFB` | no | `true` | Wrap terminal64 in `xvfb-run` for headless execution |

Either set all three of `MT5_WINEPREFIX`, `MT5_TERMINAL_PATH`,
`MT5_METAEDITOR_PATH` together (full Wine mode) or leave all three unset
(no-Wine mode). A partial configuration is a hard error.

The MT5 broker login is **not** an env var — it lives in the broker's
terminal config and is owned by the user, not the agent. The server does not
read or transmit MT5 credentials.

## Tool inventory

| Tool | Kind | Effect |
|------|------|--------|
| `mt5_register_ea` | sync | Copy a `.mq5` source into the managed workspace; return a stable `ea_handle` |
| `mt5_list_eas` | sync | List every registered EA with timestamps and SHA-256 |
| `mt5_get_ea` | sync | Return EA detail (workspace path, sha256, timestamps) |
| `mt5_remove_ea` | sync | Remove EA registration; refuses if active runs exist |
| `mt5_compile_ea` | async (queued) | Queue MetaEditor compile; returns `run_id` (status=`queued`) |
| `mt5_backtest_ea` | async (queued) | Queue Strategy Tester backtest; returns `run_id` |
| `mt5_optimize_ea` | async (queued) | Queue parameter optimization; returns `run_id` |
| `mt5_list_runs` | sync | List recent runs with optional `ea_handle` / `status` filter |
| `mt5_get_run` | sync | Return full run detail (status, summary metrics, artifacts, errors) |
| `mt5_get_run_artifact` | sync | Read a run artifact (`report` HTML/XML, `log`, `results`) as text/base64 |

All inputs and outputs are strict pydantic schemas (`extra="forbid"`),
defined in `src/mcp_metatrader5/tools/schemas.py`.

## Architecture

```
+------------------+
| MCP client       |
| (Claude / etc)   |
+--------+---------+
         | JSON-RPC over stdio
+--------v-----------------------------------------+
| server.py (FastMCP)                              |
|  - 10 tool callables                             |
|  - holds Settings, StateStore, WorkspacePaths    |
+--------+-----------------------------------------+
         |
+--------v-----------------------------------------+
| tools/  pure functions                           |
|  eas.py   compile.py  backtest.py  optimize.py   |
|  runs.py                                         |
+--------+-----------------------------------------+
         |
+--------v-----------------------------------------+
| RunManager   single asyncio.Queue worker         |
|  + WineRunnerProtocol (NullWineRunner today)     |
+--------+-----------------------------------------+
         |
+--------v-----------------------------------------+
| state.sqlite + workspace/runs/<run_id>/...       |
| INI builder + log/report parsers                 |
+--------------------------------------------------+
```

Concurrency model: a single in-process consumer drains the asyncio queue
serially. Cross-process races are prevented by the `mcp.lock` flock taken at
write sites (Wine prefix is not safe for parallel use).

## Layout

```
mcp/
  pyproject.toml
  README.md
  examples/
    claude_desktop_config.json
    mcp.json
  src/mcp_metatrader5/
    __init__.py             package metadata
    config.py               env-var settings loader
    errors.py               ErrorCode enum + typed exceptions
    logging.py              structlog + credential redactor
    state.py                SQLite store (eas, runs)
    workspace.py            path layout + slugify + flock
    server.py               FastMCP entrypoint, 10 tools
    manager.py              RunManager + WineRunnerProtocol + outcomes
    builders/ini.py         backtest/optimize INI rendering
    parsers/                compile log, HTML report, optimization XML
    runner/null.py          stub WineRunner (Phase 3 will add the real one)
    tools/                  one module per tool group + schemas
  tests/
    fixtures/               canned MT5 outputs used by parsers + e2e
    unit/                   158 unit tests
    e2e/test_full_flow.py   register → compile → backtest with fake runner
    integration/            real-Wine tests (skip unless MT5_PREFIX is set)
  workspace/                runtime: SQLite + run artifacts (gitignored)
```

## Troubleshooting

**`WINE_RUNNER_NOT_IMPLEMENTED` on compile/backtest/optimize.** Phase 3 of the
SDD change has not landed; set `MT5_TERMINAL_PATH` etc. once the real runner
is implemented. Until then, only the EA-registry and run-history tools work.

**Wine prefix not found at startup.** `MT5_WINEPREFIX` must point to a
real `WINEPREFIX` (the directory whose `drive_c/Program Files/MetaTrader 5/`
contains `terminal64.exe`). The server resolves the path with
`Path.expanduser().resolve()`; `~` works.

**Stuck `terminal.lock` after a previous crash.** Delete
`<wineprefix>/drive_c/Program Files/MetaTrader 5/terminal.lock` manually or
via `wineserver -k`. The MCP server takes its own `mcp.lock` in
`<workspace>/mcp.lock`; if a prior server crashed mid-run, restart will
reconcile in-flight rows to `failed` with `error_code=run_interrupted`.

**`xvfb-run` warnings on Wayland.** `xvfb-run` is X11-only. On Wayland sessions,
Xvfb still works because Xwayland is unrelated; the warning is benign. If the
launcher complains, install `xvfb` (`sudo apt install xvfb`).

**MT5 first-run dialog.** The first time MT5 launches under a fresh Wine
prefix it asks for a broker connection. Run the terminal interactively once
(or pre-seed `<wineprefix>/drive_c/users/<USER>/AppData/Roaming/MetaQuotes/Terminal/...`)
before the server invokes it headlessly.

**Logs are JSON; I want them readable.** Set `MT5_LOG_JSON=false`.

## Examples

See `examples/claude_desktop_config.json` for a Claude Desktop snippet, and
`examples/mcp.json` for a transport-agnostic MCP client config.

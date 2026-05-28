"""Render the default Dockerfile from a :class:`Project` row.

Pure function — no Docker side effects, no DB mutations. The contract
the spec pins:

* Every interpolated value passes through :func:`sanitize_env_value`
  BEFORE reaching Jinja. Any character outside the strict whitelist
  raises :class:`UnsafeValueError` and the caller maps that to HTTP 422.
* The same :class:`Project` row produces a **byte-identical** Dockerfile
  on every call — the Jinja2 environment is created with
  ``keep_trailing_newline=True`` and the template iterates fields in a
  deterministic order (project_id, symbol, timeframe, broker, account).
* Jinja2 ``autoescape=True`` is enabled as defence-in-depth. The
  allowlist is the primary defence; autoescape catches the (theoretical)
  case where a future template author forgets to wire a new variable
  through the sanitizer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aether_api.docker_control.sanitize import (
    UnsafeValueError,
    sanitize_env_value,
    sanitize_optional,
)
from aether_api.models.project import Project

# Templates live next to this module to keep them inside the wheel
# build (see hatch's wheel.packages — the src layout already includes
# the docker_control package).
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Single Jinja2 environment. ``autoescape`` is keyed on the file
# extension; .j2 / .Dockerfile.j2 do not match the default HTML/XML
# allowlist, so we pass ``select_autoescape(default=True)`` to force it
# on for every template — defence in depth on top of the allowlist.
_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=False,
    lstrip_blocks=False,
    keep_trailing_newline=True,
)

_DEFAULT_TEMPLATE = "default.Dockerfile.j2"


def _project_context(project: Project, *, base_image: str | None = None) -> dict[str, Any]:
    """Build the Jinja2 context dict.

    Every value that lands in the template MUST pass through the
    sanitizer here. The sanitizer is total: a value is either safe (and
    returned as-is) or it raises :class:`UnsafeValueError`.

    Field ordering inside the dict is deterministic — Python 3.7+
    dictionaries preserve insertion order, which matters for the spec's
    byte-identical-render contract.
    """
    chosen_base = base_image or project.docker_image or "mt5-base:latest"
    return {
        "base_image": sanitize_env_value(chosen_base, field="docker_image"),
        "project_id": sanitize_env_value(str(project.id), field="project_id"),
        "project_name": sanitize_optional(
            # Names may include spaces / commas / parens per
            # ProjectCreate._NAME_PATTERN, so we cannot reuse the
            # strict env-value allowlist here. The label is wrapped
            # in quotes at the template level via autoescape — but
            # we also drop everything that isn't [\w_-] so the
            # generated value stays sniff-safe.
            _label_safe(project.name),
            field="project_name",
        ),
        "symbol": sanitize_env_value(project.symbol, field="symbol"),
        "timeframe": sanitize_env_value(project.timeframe, field="timeframe"),
        "broker_name": sanitize_optional(project.broker_name, field="broker_name"),
        "account_login": sanitize_optional(project.account_login, field="account_login"),
        "account_server": sanitize_optional(project.account_server, field="account_server"),
        "account_currency": sanitize_optional(
            project.account_currency, field="account_currency"
        ),
        "mcp_port": project.mcp_port,  # int | None — no string interpolation
    }


def _label_safe(value: str) -> str:
    """Reduce a free-form name to the strict whitelist.

    Project names are validated against ``^[\\w\\- .,()/]+$`` upstream;
    Docker labels do not tolerate the relaxed characters so we map them
    to underscores rather than reject the project entirely.
    """
    out_chars: list[str] = []
    for ch in value:
        if ch.isalnum() or ch in "_-.@:/":
            out_chars.append(ch)
        else:
            out_chars.append("_")
    return "".join(out_chars) or "unnamed"


def render_default_dockerfile(
    project: Project,
    *,
    base_image: str | None = None,
) -> str:
    """Render the default Dockerfile text for ``project``.

    Raises :class:`UnsafeValueError` if any interpolated field fails the
    sanitizer. The caller (router) maps that to HTTP 422 with the
    offending field name.

    Returns deterministic text — repeated calls for the same project row
    return byte-identical output.
    """
    context = _project_context(project, base_image=base_image)
    template = _ENV.get_template(_DEFAULT_TEMPLATE)
    return template.render(context)


__all__ = [
    "UnsafeValueError",
    "render_default_dockerfile",
]

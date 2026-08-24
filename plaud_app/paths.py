"""Where this app keeps state that must survive a container recreation.

``<package_dir>`` (``ctx.package_dir``) is where ``mcp.json`` lives — see
``mcp_config.py`` — and is disposable: an app update is uninstall + install,
which takes the package dir with it. Nothing durable belongs there.

``$AW_WORKSPACE_HOME/data/plaud/`` is this app's durable root instead — same
location and reasoning as aw-app-google-workspace-mcp's
``credentials_dir()``. It holds the official `@plaud-ai/mcp` CLI's own
isolated ``HOME`` (see ``cli_login.py``): pointing that CLI's `HOME` here
both keeps its `~/.claude` writes off the real workspace home AND makes its
`~/.plaud/tokens-mcp.json` survive a container recreation, in one setting.

Nothing here imports the host runtime (``src.apps.paths``): a Tier-1 app only
touches the host through its ``ctx`` facades and plain env vars, and this
repo has no ``src/`` tree of its own to import from in CI.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_ID = "plaud"
DEFAULT_WORKSPACE_HOME = "/opt/aw-workspace/.aw-workspace"


def workspace_home() -> Path:
    return Path(os.environ.get("AW_WORKSPACE_HOME") or DEFAULT_WORKSPACE_HOME)


def data_dir() -> Path:
    path = workspace_home() / "data" / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path

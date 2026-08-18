"""Entry describing this app's own ``/mcp`` endpoint, for aw-mcp-gateway's
app-scan (``scan_app_mcp_servers()``, which reads
``<installed-app-dir>/mcp.json``).

Ported pattern from ``aw-app-notion``'s ``notion_app/mcp/self_register.py``
(itself mirroring ``aw-app-diff-tool``): the monolith's ``src/mcp/aw_plaud.py``
was a stdio subprocess reading ``PLAUD_BEARER_TOKEN`` straight from its own
environment. Reproducing that here would mean the gateway container holding
a copy of the token with no path to obtain or refresh it. Serving MCP from
this app's own already-authenticated route sidesteps that: the token lives
in this app's secret store and every tool call reads it fresh.
"""
from __future__ import annotations

import os
import socket

MCP_SERVER_NAME = "aw-plaud"
ROUTE_PATH = "/api/apps/plaud/mcp"


def build_self_entry(port: int | None = None) -> dict:
    """The ``mcpServers`` entry pointing at this app's ``POST /mcp``.

    Tier-1 (in-process): this *is* the aw-workspace process, so
    ``socket.gethostname()`` is exactly the value ContainerSupervisor injects
    into sibling containers as ``AW_WORKSPACE_HOST``, and
    ``AW_WORKSPACE_API_KEY`` is already in this process's own environment —
    nothing has to be provisioned.
    """
    host = socket.gethostname()
    port = port or int(os.environ.get("AW_PORT") or 9030)
    entry: dict = {
        "type": "http",
        "url": f"http://{host}:{port}{ROUTE_PATH}",
        "enabled": True,
    }
    api_key = os.environ.get("AW_WORKSPACE_API_KEY")
    if api_key:
        entry["headers"] = {"X-Api-Key": api_key}
    return entry

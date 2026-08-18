"""Builds this app's own root ``mcp.json`` — the file aw-mcp-gateway's
app-scan reads directly (same contract as aw-app-notion/aw-app-mcp-tools).

Just one server here (unlike aw-app-notion's two): this app's own 5 Plaud
tools, served in-process over Streamable HTTP at ``/api/apps/plaud/mcp``
(see ``mcp/http_handler.py``). Advertised only once a token has been saved —
the tools would just fail immediately without one, and a server that fails
every call is worse than a server that isn't listed at all.
"""
from __future__ import annotations

import json
from pathlib import Path

from .mcp import self_register

SERVER_NAME = self_register.MCP_SERVER_NAME


def build_mcp_servers(token: str | None, *, port: int | None = None) -> dict:
    """The ``mcpServers`` object this app's root mcp.json should contain.
    Empty when no token has been saved yet."""
    if not token:
        return {}
    return {SERVER_NAME: self_register.build_self_entry(port)}


def write_mcp_json(package_dir: str, token: str | None, *, port: int | None = None) -> dict:
    """Regenerate this app's own root mcp.json from the stored token and
    write it to disk. Returns the full ``{"mcpServers": ...}`` document
    written."""
    doc = {"mcpServers": build_mcp_servers(token, port=port)}
    path = Path(package_dir) / "mcp.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc

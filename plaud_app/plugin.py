"""
Entrypoint referenced by aw-app.json's runtime.entrypoint
("plaud_app.plugin:PlaudAppPlugin").

Plugs into the real F4 framework runtime: activate(ctx) (1) regenerates this
app's own root mcp.json from whatever token is already in the secret store
(picks up a token saved before a workspace recreation/reconcile — same
reasoning as aw-app-notion re-writing its mcp.json on activate), and
(2) registers this app's sub-app from routes.py THROUGH the gated
``ctx.routes`` facade (capability ``routes:register``), mounted by the
runtime at ``/api/apps/plaud``.

The generated mcp.json's single entry embeds this process's hostname and API
key, both of which change when the workspace container is recreated — so it
is rebuilt on every activate rather than persisted.

No system CLI to install and no standalone mode: every route here needs
``ctx.secrets`` (the token store) and ``ctx.package_dir`` (where this app's
own mcp.json gets regenerated), same reasoning as aw-app-notion/aw-app-git.
"""
from __future__ import annotations

import logging
import os

from . import mcp_config
from . import routes as routes_mod

log = logging.getLogger("aw_apps.plaud")


class PlaudAppPlugin:
    async def activate(self, ctx) -> None:
        self._ctx = ctx
        token = ctx.secrets.read(routes_mod.TOKEN_KEY)
        doc = self._write_mcp_json(ctx, token)

        ctx.routes.register(routes_mod.build_routes(ctx))

        log.info(
            "aw-app-plaud activated: mcp servers=%s, routes mounted",
            sorted(doc["mcpServers"]) or "none",
        )

    def _write_mcp_json(self, ctx, token: str | None) -> dict:
        port = int(os.environ.get("AW_PORT") or 9030)
        return mcp_config.write_mcp_json(ctx.package_dir, token, port=port)

    async def deactivate(self) -> None:
        log.info("aw-app-plaud deactivated")

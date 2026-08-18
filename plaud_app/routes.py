"""
plaud_app's backend sub-app.

No standalone mode — every route here needs ``ctx.secrets`` (the token
store) and ``ctx.package_dir`` (where this app's own ``mcp.json`` gets
regenerated), both of which only exist inside the real F4 framework runtime.
Same reasoning as aw-app-notion/aw-app-git.

``build_routes(ctx)`` is called once from ``plugin.py``'s ``activate()`` and
mounted at ``/api/apps/plaud`` behind the runtime's ``IdentityGuard``.

The token is intentionally **not** routed through the generic
``POST /api/apps/plaud/config`` endpoint (plain, cloud-syncable app config).
``POST /settings`` goes straight to ``ctx.secrets`` instead — same pattern as
aw-app-notion's ``notion_token`` / aw-app-git's ``github_token``.
"""
from __future__ import annotations

import logging

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import mcp_config
from .client import PlaudClient, PlaudError, format_expiry_text, normalize_pasted_token

log = logging.getLogger("aw_apps.plaud.routes")

TOKEN_KEY = "plaud_bearer_token"


def _status_payload(client: PlaudClient, doc_servers: dict) -> dict:
    st = client.status()
    return {
        # "logged_in" is what a status-bound widget would look for;
        # "configured" is the same value under the name every other caller
        # (tests, /mcp.json) expects.
        "logged_in": bool(st.valid),
        "configured": st.configured,
        "expired": st.expired,
        "email": st.email,
        "expires_at": st.expires_at,
        "expires_in_text": format_expiry_text(st.expires_at),
        "error": st.error,
        "mcp_server_enabled": bool(doc_servers),
    }


def build_routes(ctx) -> FastAPI:
    app = FastAPI(title="plaud")

    # Resolved per call, never snapshotted: a token saved (or cleared) at
    # runtime has to take effect without a restart.
    client = PlaudClient(lambda: ctx.secrets.read(TOKEN_KEY))

    def _guard(fn, *args, **kwargs):
        """Run a Plaud API call, turning a PlaudError into its real HTTP
        status instead of a 500 — a 401 ('token expired') and a 404 ('no
        transcript yet') are different problems the UI needs to render
        differently."""
        try:
            return fn(*args, **kwargs)
        except PlaudError as exc:
            status = exc.status if 400 <= exc.status < 600 else 502
            return JSONResponse({"ok": False, "error": str(exc), "expired": exc.expired}, status_code=status)

    @app.get("/status")
    async def status() -> dict:
        token = ctx.secrets.read(TOKEN_KEY)
        return _status_payload(client, mcp_config.build_mcp_servers(token))

    @app.post("/settings")
    async def save_settings(data: dict = Body(...)) -> dict:
        """Stores the token and regenerates mcp.json. Deliberately does NOT
        call out to Plaud here — that's what GET /status is for, and the UI
        calls it right after saving. Keeps this route fast and independent
        of Plaud being reachable.

        Accepts whatever shape DevTools' "Copy value" actually produces —
        see ``normalize_pasted_token``: with/without ``Bearer ``, surrounding
        whitespace, or the whole ``authorization: eyJ...`` header line."""
        token = normalize_pasted_token(data.get(TOKEN_KEY))
        if not token:
            return JSONResponse({"error": f"{TOKEN_KEY} is required"}, status_code=400)
        ctx.secrets.write(TOKEN_KEY, token)
        doc = mcp_config.write_mcp_json(ctx.package_dir, token)
        return {"ok": True, "logged_in": True, "configured": True,
                "mcp_server_enabled": bool(doc["mcpServers"])}

    @app.post("/logout")
    async def clear_token() -> dict:
        ctx.secrets.delete(TOKEN_KEY)
        mcp_config.write_mcp_json(ctx.package_dir, None)
        return {"ok": True, "logged_in": False, "configured": False}

    @app.get("/mcp.json")
    async def mcp_json() -> dict:
        token = ctx.secrets.read(TOKEN_KEY)
        return {"mcpServers": mcp_config.build_mcp_servers(token)}

    # ------------------------------------------------------------------
    # Recordings — the UI's data surface. Not a REST mirror of the MCP
    # tools (unlike aw-app-notion's kanban routes): these return
    # UI-shaped JSON (structured transcript segments, not preformatted
    # text) rather than the MCP tools' plain-text blobs.
    # ------------------------------------------------------------------

    @app.get("/recordings")
    async def recordings(limit: int = 20, skip: int = 0):
        return _guard(client.list_recordings, limit=limit, skip=skip)

    @app.get("/recordings/{file_id}/transcript")
    async def transcript(file_id: str):
        result = _guard(client.get_transcript, file_id)
        if isinstance(result, JSONResponse):
            return result
        return {"segments": result}

    @app.get("/recordings/{file_id}/summary")
    async def summary(file_id: str):
        result = _guard(client.get_summary, file_id)
        if isinstance(result, JSONResponse):
            return result
        return {"summary": result}

    @app.get("/recordings/{file_id}/audio")
    async def audio(file_id: str, download: bool = False):
        """Streams the original audio through this app so the token never
        reaches the browser. ``download=true`` adds Content-Disposition so
        the browser saves it instead of trying to play it inline — same
        bytes either way, no second round-trip to Plaud."""
        try:
            resp, http_client = client.audio_stream(file_id)
        except PlaudError as exc:
            status = exc.status if 400 <= exc.status < 600 else 502
            return JSONResponse({"ok": False, "error": str(exc), "expired": exc.expired}, status_code=status)

        def _iter():
            try:
                for chunk in resp.iter_bytes():
                    yield chunk
            finally:
                resp.close()
                http_client.close()

        headers = {}
        if download:
            ext = (resp.headers.get("content-type", "").split("/")[-1] or "audio").split(";")[0]
            headers["Content-Disposition"] = f'attachment; filename="{file_id}.{ext}"'
        content_length = resp.headers.get("content-length")
        if content_length:
            headers["Content-Length"] = content_length
        return StreamingResponse(
            _iter(),
            media_type=resp.headers.get("content-type", "audio/mpeg"),
            headers=headers,
        )

    # ------------------------------------------------------------------
    # MCP — Streamable HTTP, auto-discovered by aw-mcp-gateway's app-scan
    # (see mcp/self_register.py + mcp/http_handler.py).
    # ------------------------------------------------------------------

    @app.post("/mcp")
    async def mcp_post(data: dict | list = Body(...)):
        from .mcp.http_handler import handle_request as mcp_handle_request

        messages = data if isinstance(data, list) else [data]
        responses = []
        for m in messages:
            r = await mcp_handle_request(m, client=client)
            if r is not None:
                responses.append(r)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses if isinstance(data, list) else responses[0])

    @app.get("/mcp")
    async def mcp_get():
        return Response(status_code=405)

    return app

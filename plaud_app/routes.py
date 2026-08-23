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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from . import mcp_config, oauth
from .client import PlaudClient, PlaudError, format_expiry_text, normalize_pasted_token

log = logging.getLogger("aw_apps.plaud.routes")

TOKEN_KEY = "plaud_bearer_token"


def _oauth_error_text(ctx) -> str | None:
    """The one sentence the settings panel's `error_text` shows for
    whichever non-happy OAuth state currently applies, checked in priority
    order. `None` means OAuth has nothing to report right now (either fully
    connected, or never touched at all)."""
    missing = oauth.missing_config(ctx)
    if missing:
        return f"OAuth not configured — missing: {', '.join(missing)}."
    if oauth.is_connected(ctx):
        _token, error = oauth.get_valid_access_token(ctx)
        if error:
            return f"Token expired and refresh failed: {error}. Reconnect below."
    return None


def _status_payload(ctx, client: PlaudClient, doc_servers: dict) -> dict:
    st = client.status()
    oauth_connected = oauth.is_connected(ctx)
    bearer_present = bool(ctx.secrets.read(TOKEN_KEY))
    auth_method = "oauth" if oauth_connected else ("bearer" if bearer_present else None)
    oauth_missing = oauth.missing_config(ctx)
    error = st.error if (bearer_present and not oauth_connected) else _oauth_error_text(ctx)
    # An OAuth access token has no reason to be a JWT (could be opaque), so
    # PlaudClient.status()'s exp-claim-derived expires_at doesn't apply to
    # it — use the expiry oauth.py tracked from the token response instead.
    expires_at = oauth.token_expiry(ctx) if oauth_connected else st.expires_at
    return {
        # "logged_in" is what a status-bound widget would look for;
        # "configured" is the same value under the name every other caller
        # (tests, /mcp.json) expects. `oauth_missing` being non-empty also
        # counts as "configured" — there is something the panel needs to
        # say (which credentials are missing) even before anything has
        # ever been connected.
        "logged_in": bool(st.valid),
        "configured": st.configured or bool(oauth_missing),
        "expired": st.expired,
        "email": st.email,
        "expires_at": expires_at,
        "expires_in_text": format_expiry_text(expires_at),
        "error": error,
        "auth_method": auth_method,
        "oauth_configured": not oauth_missing,
        "oauth_missing": oauth_missing,
        "mcp_server_enabled": bool(doc_servers),
    }


def _mcp_gate_token(ctx) -> str | None:
    """Whatever truthy sentinel gates `mcp_config.write_mcp_json` — the
    string's *content* is never used (this app's mcp.json entry always
    points at its own already-authenticated /mcp route, see
    mcp/self_register.py), only its presence. OAuth being the connected
    method still has to enable the MCP server exactly like a pasted bearer
    token always did."""
    bearer = ctx.secrets.read(TOKEN_KEY)
    if bearer:
        return bearer
    return "oauth-connected" if oauth.is_connected(ctx) else None


def build_routes(ctx) -> FastAPI:
    app = FastAPI(title="plaud")

    # Resolved per call, never snapshotted: a token saved (or cleared) at
    # runtime has to take effect without a restart. OAuth first (refreshed
    # transparently), the pasted bearer token as a fallback — see oauth.py.
    client = PlaudClient(oauth.make_token_getter(ctx, lambda: ctx.secrets.read(TOKEN_KEY)))

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
        return _status_payload(ctx, client, mcp_config.build_mcp_servers(_mcp_gate_token(ctx)))

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
        """Disconnects BOTH auth methods — whichever is active, "Logout"
        means fully disconnected, not "disconnect only the one you happened
        to use last"."""
        ctx.secrets.delete(TOKEN_KEY)
        oauth.disconnect(ctx)
        mcp_config.write_mcp_json(ctx.package_dir, None)
        return {"ok": True, "logged_in": False, "configured": False}

    @app.get("/mcp.json")
    async def mcp_json() -> dict:
        return {"mcpServers": mcp_config.build_mcp_servers(_mcp_gate_token(ctx))}

    # ------------------------------------------------------------------
    # OAuth — see oauth.py's module docstring for why plaud_oauth_
    # authorize_url / plaud_oauth_token_url are config, not constants.
    # ------------------------------------------------------------------

    @app.get("/oauth/start")
    async def oauth_start():
        """A real browser navigation target (the settings panel's markdown
        "Connect" link points straight here — see windows/settings.json),
        not a fetch(): on success this redirects the browser to Plaud, on
        failure it renders the reason in the same tab in plain text."""
        try:
            authorize_url = oauth.start_authorization(ctx)
        except oauth.OAuthError as exc:
            return Response(content=str(exc), status_code=400, media_type="text/plain")
        return RedirectResponse(authorize_url, status_code=302)

    @app.get("/oauth/callback")
    async def oauth_callback(code: str | None = None, state: str | None = None,
                             error: str | None = None, error_description: str | None = None):
        """Plaud redirects the browser here after the user authorizes (or
        declines). Renders a plain HTML page — this tab has no SPA loaded,
        it's a fresh top-level navigation to this app's own backend."""
        try:
            oauth.handle_callback(ctx, code=code, state=state,
                                  error=error, error_description=error_description)
        except oauth.OAuthError as exc:
            return HTMLResponse(
                f"<p>Could not connect Plaud: {exc}</p>"
                f"<p>You can close this tab and try again from Settings.</p>",
                status_code=400,
            )
        mcp_config.write_mcp_json(ctx.package_dir, _mcp_gate_token(ctx))
        return HTMLResponse(
            "<p>Connected to Plaud.</p><p>You can close this tab.</p>"
            "<script>try { window.close(); } catch (e) {}</script>"
        )

    @app.post("/oauth/disconnect")
    async def oauth_disconnect() -> dict:
        oauth.disconnect(ctx)
        mcp_config.write_mcp_json(ctx.package_dir, _mcp_gate_token(ctx))
        return {"ok": True}

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

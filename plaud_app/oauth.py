"""Plaud OAuth (authorization code + PKCE) — the app as its own OAuth client.

This is the mechanism, not a working integration: as of 2026-08-23 Plaud has
not published the authorize/token endpoint URLs for this flow, so every
network call here is dead code until they are configured (see below). Wiring
it up is deliberately config-driven rather than hardcoded, so completing the
feature later is "paste two URLs", not a redesign.

Why this can't be hardcoded (the investigation this module is built from):

* Plaud has TWO separate developer products, easy to conflate (the Kanban
  card that requested this feature did): **Plaud Embedded**
  (docs.plaud.ai/plaud-embedded, GA) is a partner-provisions-new-users
  model — ``POST {region-host}/developer/api/oauth/partner/access-token``
  (client_id + secret_key, Basic auth) mints a partner token, then
  ``POST .../open/partner/users/access-token`` mints tokens for users the
  PARTNER creates. There is no path from this API to an existing personal
  account's own recordings.
* The **Plaud OAuth API** — browser consent, an existing user authorizing a
  third party to read THEIR OWN recordings/transcripts, which is what this
  app needs — is a *different*, still-private-beta product
  (support.plaud.ai/hc/en-us/articles/56061278749209). Per Plaud's own FAQ,
  access is waitlist-only and endpoint details are "provided once the API
  is available" — i.e. not published even to accepted beta members yet.
* The URL the card's design assumed as the authorize page
  (``https://web.plaud.ai/platform/oauth``) was checked live: it silently
  drops unrecognized query params (client_id, redirect_uri, response_type,
  code_challenge all ignored) and redirects an unauthenticated visitor to
  ``/login?from_url=/platform/oauth/workspace-select`` — the signature of
  an internal multi-workspace selector in Plaud's own web app, not a
  third-party consent screen.

So ``plaud_oauth_authorize_url`` / ``plaud_oauth_token_url`` are plain
config fields (see aw-app.json's config_schema), left empty until Plaud's
OAuth API team hands Frederico real ones off the waitlist. Everything below
this line works against whatever those two fields hold — PKCE generation,
the pending-state handshake, token storage, refresh, and the "here's
exactly what's missing" status reporting the settings panel renders.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets as pysecrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

log = logging.getLogger("aw_apps.plaud.oauth")

CLIENT_ID_KEY = "plaud_client_id"
CLIENT_SECRET_KEY = "plaud_client_secret"
AUTHORIZE_URL_KEY = "plaud_oauth_authorize_url"
TOKEN_URL_KEY = "plaud_oauth_token_url"

PENDING_SECRET_KEY = "plaud_oauth_pending"
TOKENS_SECRET_KEY = "plaud_oauth_tokens"

CALLBACK_PATH = "/api/apps/plaud/oauth/callback"
PENDING_TTL_S = 600  # 10 minutes to complete the browser round trip
REFRESH_MARGIN_S = 60  # refresh a little before the token actually expires


class OAuthError(RuntimeError):
    """Raised for any failure in the OAuth handshake itself (as opposed to
    ``PlaudError``, which is a failure of an already-authenticated API
    call). ``revoked`` flags a failure that means the stored tokens are
    dead for good (invalid_grant et al) — the caller clears them so the
    connection state degrades to a clean 'disconnected' rather than a
    'reconnect' loop."""

    def __init__(self, message: str, *, revoked: bool = False):
        super().__init__(message)
        self.revoked = revoked


# ── config ──────────────────────────────────────────────────────────────

def _oauth_config(ctx) -> dict:
    return {
        "client_id": (ctx.config.get(CLIENT_ID_KEY) or "").strip(),
        "client_secret": (ctx.secrets.read(CLIENT_SECRET_KEY) or "").strip(),
        "authorize_url": (ctx.config.get(AUTHORIZE_URL_KEY) or "").strip(),
        "token_url": (ctx.config.get(TOKEN_URL_KEY) or "").strip(),
    }


def missing_config(ctx) -> list[str]:
    """Which of the 4 prerequisites for the OAuth flow aren't set yet, in a
    stable order — the settings panel lists these verbatim."""
    cfg = _oauth_config(ctx)
    keys = [CLIENT_ID_KEY, CLIENT_SECRET_KEY, AUTHORIZE_URL_KEY, TOKEN_URL_KEY]
    field_names = ["client_id", "client_secret", "authorize_url", "token_url"]
    return [key for key, field in zip(keys, field_names) if not cfg[field]]


def is_configured(ctx) -> bool:
    return not missing_config(ctx)


def redirect_uri(ctx) -> str | None:
    """This workspace's own public callback URL, or ``None`` if the
    workspace hasn't published one yet (``AW_WORKSPACE_API_URL`` — set at
    boot by ``src.api.workspace_url.publish_workspace_api_url()``; unset in
    a dev/test process with no public tunnel)."""
    base = (os.environ.get("AW_WORKSPACE_API_URL") or "").strip().rstrip("/")
    return f"{base}{CALLBACK_PATH}" if base else None


# ── PKCE ────────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce_pair() -> tuple[str, str]:
    """``(code_verifier, code_challenge)`` — RFC 7636 S256. The verifier is
    kept server-side only (see module docstring point 5 of the Kanban
    card's design); the browser only ever sees the challenge."""
    verifier = _b64url(pysecrets.token_bytes(40))  # 40 bytes -> 53 chars, within the 43-128 range
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ── pending state (state -> code_verifier, across the browser round trip) ─

def _read_pending(ctx) -> dict | None:
    raw = ctx.secrets.read(PENDING_SECRET_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if time.time() - float(data.get("created_at", 0)) > PENDING_TTL_S:
        return None
    return data


def _write_pending(ctx, *, state: str, code_verifier: str) -> None:
    ctx.secrets.write(PENDING_SECRET_KEY, json.dumps({
        "state": state, "code_verifier": code_verifier, "created_at": time.time(),
    }))


def _clear_pending(ctx) -> None:
    ctx.secrets.delete(PENDING_SECRET_KEY)


# ── tokens ──────────────────────────────────────────────────────────────

@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: float | None


def _read_tokens(ctx) -> OAuthTokens | None:
    raw = ctx.secrets.read(TOKENS_SECRET_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not data.get("access_token"):
        return None
    return OAuthTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=data.get("expires_at"),
    )


def _write_tokens(ctx, tokens: OAuthTokens) -> None:
    ctx.secrets.write(TOKENS_SECRET_KEY, json.dumps({
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
    }))


def token_expiry(ctx) -> float | None:
    """The OAuth access token's known expiry (from the token response's own
    ``expires_in``, computed at exchange/refresh time) — unlike
    ``PlaudClient.status()``'s ``expires_at``, this doesn't depend on the
    access token happening to be a JWT with a decodable ``exp`` claim,
    which an OAuth-issued token has no reason to be (could be opaque)."""
    tokens = _read_tokens(ctx)
    return tokens.expires_at if tokens else None


def is_connected(ctx) -> bool:
    """Tokens are on file, regardless of whether they're still valid — same
    presence-only semantics ``mcp_config.build_mcp_servers`` already uses
    for the pasted bearer token."""
    return _read_tokens(ctx) is not None


def disconnect(ctx) -> None:
    ctx.secrets.delete(TOKENS_SECRET_KEY)
    _clear_pending(ctx)


# ── token-response parsing (shared by exchange + refresh) ────────────────

def _parse_token_response(resp: httpx.Response) -> dict:
    """Same regression this app's own client.py guards against for
    api.plaud.ai (v0.2.1 — HTTP 200 with the real error in the JSON body,
    observed as ``{"status": -419, ...}``): a new OAuth token endpoint could
    just as easily wrap its errors the same way, and a plain
    ``raise_for_status()`` would read that as success."""
    try:
        data = resp.json()
    except Exception as exc:
        raise OAuthError(f"token endpoint returned non-JSON: {exc}") from exc
    body_status = data.get("status") if isinstance(data, dict) else None
    if isinstance(body_status, (int, float)) and body_status < 0:
        msg = (data.get("msg") or "").strip() or f"error {body_status}"
        raise OAuthError(f"token endpoint error in response body: {msg}",
                         revoked=body_status == -419)
    if resp.status_code >= 400:
        err = data.get("error") if isinstance(data, dict) else None
        desc = data.get("error_description") if isinstance(data, dict) else None
        revoked = err in ("invalid_grant", "invalid_client", "unauthorized_client")
        raise OAuthError(f"token endpoint {resp.status_code}: {err or desc or resp.text[:300]}",
                         revoked=revoked)
    if not isinstance(data, dict) or not data.get("access_token"):
        raise OAuthError(f"token endpoint response missing access_token: {resp.text[:300]}")
    return data


def _tokens_from_response(data: dict) -> OAuthTokens:
    expires_in = data.get("expires_in")
    expires_at = time.time() + float(expires_in) if expires_in is not None else None
    return OAuthTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
    )


# ── the flow itself ────────────────────────────────────────────────────

def start_authorization(ctx) -> str:
    """Mints PKCE + state, stashes the verifier server-side, and returns the
    URL to redirect the browser to. Raises OAuthError (never guesses) if
    the app isn't configured to do this yet."""
    missing = missing_config(ctx)
    if missing:
        raise OAuthError(f"OAuth is not configured — missing: {', '.join(missing)}")
    uri = redirect_uri(ctx)
    if not uri:
        raise OAuthError(
            "This workspace has no published public URL (AW_WORKSPACE_API_URL "
            "is unset) — Plaud can't redirect back to it.")

    cfg = _oauth_config(ctx)
    verifier, challenge = pkce_pair()
    state = _b64url(pysecrets.token_bytes(24))
    _write_pending(ctx, state=state, code_verifier=verifier)

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    sep = "&" if "?" in cfg["authorize_url"] else "?"
    return f"{cfg['authorize_url']}{sep}{urlencode(params)}"


def handle_callback(ctx, *, code: str | None, state: str | None,
                    error: str | None = None, error_description: str | None = None) -> None:
    """Completes the round trip: validates ``state`` against what
    ``start_authorization`` stashed, exchanges ``code`` for tokens, and
    persists them. Raises OAuthError on any failure — the route renders
    whatever message this carries."""
    if error:
        raise OAuthError(error_description or error)
    pending = _read_pending(ctx)
    _clear_pending(ctx)  # one-shot regardless of outcome — never replay a code
    if not pending:
        raise OAuthError("This authorization link expired or was already used — try connecting again.")
    if not state or state != pending["state"]:
        raise OAuthError("State mismatch — this callback doesn't match a connection attempt we started.")
    if not code:
        raise OAuthError("Plaud did not send an authorization code.")

    cfg = _oauth_config(ctx)
    uri = redirect_uri(ctx)
    try:
        resp = httpx.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": uri,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "code_verifier": pending["code_verifier"],
            },
            headers={"accept": "application/json"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise OAuthError(f"request to Plaud's token endpoint failed: {exc}") from exc

    data = _parse_token_response(resp)
    _write_tokens(ctx, _tokens_from_response(data))


def _refresh(ctx, refresh_token: str) -> OAuthTokens:
    cfg = _oauth_config(ctx)
    try:
        resp = httpx.post(
            cfg["token_url"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
            },
            headers={"accept": "application/json"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise OAuthError(f"request to Plaud's token endpoint failed: {exc}") from exc
    data = _parse_token_response(resp)
    tokens = _tokens_from_response(data)
    if not tokens.refresh_token:
        # Some providers omit refresh_token on a refresh response, meaning
        # "unchanged" — carry the old one forward instead of losing it.
        tokens.refresh_token = refresh_token
    return tokens


def get_valid_access_token(ctx) -> tuple[str | None, str | None]:
    """``(token, error)``.

    * Never connected via OAuth: ``(None, None)`` — callers fall back to
      the pasted bearer token, unchanged from before this module existed.
    * Connected and (still, or freshly refreshed) valid: ``(token, None)``.
    * Connected but expired and refresh failed: ``(None, "<message>")`` —
      the caller must NOT fall back to the pasted token here; this is its
      own distinct "expired / refresh failed" state.
    * Refresh failed because Plaud revoked the grant: tokens are cleared
      and this returns ``(None, None)`` — same as "never connected", so
      the UI shows a plain "disconnected" instead of a stuck error state
      (the card's "revogado ... sem loop de retry" requirement).
    """
    tokens = _read_tokens(ctx)
    if tokens is None:
        return None, None
    if tokens.expires_at is None or tokens.expires_at - time.time() > REFRESH_MARGIN_S:
        return tokens.access_token, None
    if not tokens.refresh_token:
        disconnect(ctx)
        return None, None
    try:
        fresh = _refresh(ctx, tokens.refresh_token)
    except OAuthError as exc:
        if exc.revoked:
            disconnect(ctx)
            return None, None
        log.warning("plaud oauth: refresh failed (%s) — keeping stored tokens for a retry", exc)
        return None, str(exc)
    _write_tokens(ctx, fresh)
    return fresh.access_token, None


def make_token_getter(ctx, fallback):
    """Builds the callable handed to ``PlaudClient(token_getter=...)``:
    OAuth first (refreshing transparently), the pasted bearer token as a
    fallback for as long as OAuth has never been connected — same contract
    ``PlaudClient`` already had, just with a smarter source. ``fallback`` is
    a zero-arg callable, normally ``lambda: ctx.secrets.read(TOKEN_KEY)``."""
    def _get() -> str | None:
        token, error = get_valid_access_token(ctx)
        if token:
            return token
        if error:
            # Connected but currently broken — do not silently fall back to
            # a possibly-stale pasted token and hide that from the caller.
            return None
        return fallback()
    return _get

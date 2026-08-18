"""Client for the personal Plaud account (unofficial web API).

Ported from agentic-workspace's ``src/mcp/aw_plaud.py``, which was a stdio
MCP subprocess reading ``PLAUD_BEARER_TOKEN`` from its own environment. Here
the token lives in this app's secret store (``ctx.secrets`` — see
``routes.py``/``plugin.py``) and every call reads it fresh, so a re-pasted
token takes effect on the very next request with no restart.

Talks directly to Plaud's own ``api.plaud.ai`` REST API — the same one
``web.plaud.ai`` itself calls — authenticated with a bearer token lifted from
a logged-in browser session. There is no supported public API yet (the
official OAuth API is in private beta as of 2026-07); see
``skills/aw-plaud/SKILL.md`` for how to obtain/refresh the token and the two
gotchas this client carries forward:

1. **Browser-shaped User-Agent required.** Cloudflare in front of
   ``api.plaud.ai`` blocks Python's default HTTP client UA as a bot signature
   (403 / error 1010) — every request, including the raw audio download,
   sends an explicit Chrome-shaped ``user-agent``.
2. **No refresh flow.** The token is a short-lived JWT (~24h TTL observed).
   ``decode_token_exp`` parses (never verifies) its ``exp`` claim so the UI
   can show "expires in Xh" / "expired" proactively, instead of the
   connection only going red after a tool call 401s.
"""
from __future__ import annotations

import base64
import gzip
import json
import time
from dataclasses import dataclass
from typing import Callable

import httpx

API_BASE = "https://api.plaud.ai"
# See module docstring point 1 — a browser-shaped UA is required even though
# the request is otherwise a plain authenticated API call.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


class PlaudError(RuntimeError):
    """Raised for any Plaud API failure. ``status`` is the HTTP status when
    known (0 for a network-level failure), ``expired`` flags a 401 so callers
    can tell "token expired" apart from "Plaud is down" without re-parsing
    the message."""

    def __init__(self, message: str, *, status: int = 0, expired: bool = False):
        super().__init__(message)
        self.status = status
        self.expired = expired


def decode_token_exp(token: str) -> float | None:
    """Best-effort, unverified parse of a JWT's ``exp`` claim (seconds since
    epoch). Returns ``None`` for anything that doesn't look like a JWT with
    one — this is a UX hint (show "expires soon"), never an auth decision;
    the API call itself is the actual check."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return float(exp) if exp is not None else None
    except Exception:
        return None


@dataclass
class TokenStatus:
    configured: bool
    valid: bool | None  # None = not checked (no token)
    expired: bool
    email: str | None
    expires_at: float | None
    error: str | None


class PlaudClient:
    """Thin wrapper over ``api.plaud.ai``. ``token_getter`` is called on
    every request (never cached) so a freshly-saved or cleared token is
    picked up immediately — same pattern as ``NotionClient`` in
    aw-app-notion."""

    def __init__(self, token_getter: Callable[[], str | None]):
        self._token_getter = token_getter

    def _bearer(self) -> str | None:
        tok = (self._token_getter() or "").strip()
        if not tok:
            return None
        return tok if tok.lower().startswith("bearer ") else f"Bearer {tok}"

    def raw_token(self) -> str | None:
        tok = (self._token_getter() or "").strip()
        return tok or None

    def _headers(self, *, accept_json: bool = True) -> dict:
        bearer = self._bearer()
        if not bearer:
            raise PlaudError("PLAUD_BEARER_TOKEN is not configured.")
        headers = {
            "authorization": bearer,
            "app-platform": "web",
            "user-agent": BROWSER_UA,
        }
        if accept_json:
            headers["accept"] = "application/json, */*"
        return headers

    def _get_json(self, path: str, *, timeout: float = 20.0) -> dict:
        try:
            resp = httpx.get(f"{API_BASE}{path}", headers=self._headers(), timeout=timeout)
        except httpx.HTTPError as exc:
            raise PlaudError(f"request to Plaud failed: {exc}") from exc
        if resp.status_code == 401:
            raise PlaudError(
                "Plaud API returned 401 Unauthorized — the token has expired. "
                "Re-paste a fresh token (see the aw-plaud skill).",
                status=401, expired=True,
            )
        if resp.status_code >= 400:
            raise PlaudError(f"Plaud API {resp.status_code}: {resp.text[:300]}", status=resp.status_code)
        try:
            return resp.json()
        except Exception as exc:
            raise PlaudError(f"Plaud API returned non-JSON: {exc}") from exc

    # ── status ──────────────────────────────────────────────────────────

    def status(self) -> TokenStatus:
        token = self.raw_token()
        if not token:
            return TokenStatus(configured=False, valid=None, expired=False,
                               email=None, expires_at=None, error=None)
        expires_at = decode_token_exp(token)
        expired_by_claim = expires_at is not None and expires_at <= time.time()
        try:
            data = self._get_json("/user/me")
        except PlaudError as exc:
            return TokenStatus(
                configured=True, valid=False, expired=exc.expired or expired_by_claim,
                email=None, expires_at=expires_at, error=str(exc),
            )
        user = data.get("data_user") or {}
        email = user.get("email") or user.get("nickname")
        return TokenStatus(configured=True, valid=True, expired=False,
                           email=email, expires_at=expires_at, error=None)

    # ── recordings ──────────────────────────────────────────────────────

    def list_recordings(self, limit: int = 20, skip: int = 0) -> list[dict]:
        data = self._get_json(
            f"/file/simple/web?skip={skip}&limit={limit}&is_trash=0&sort_by=start_time&is_desc=true"
        )
        files = data.get("data_file_list") or []
        return [
            {
                "id": f["id"],
                "filename": f.get("filename") or "(untitled)",
                "duration_s": (f.get("duration") or 0) // 1000,
                "start_time_ms": f.get("start_time"),
                "is_transcribed": bool(f.get("is_trans")),
                "has_summary": bool(f.get("is_summary")),
            }
            for f in files
        ]

    def _content_link(self, file_id: str, data_type: str) -> str | None:
        detail = self._get_json(f"/file/detail/{file_id}")
        for item in (detail.get("data") or {}).get("content_list", []):
            if item.get("data_type") == data_type and item.get("data_link"):
                return item["data_link"]
        return None

    def _fetch_gz_text(self, url: str) -> str:
        try:
            resp = httpx.get(url, headers={"user-agent": BROWSER_UA}, timeout=20.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PlaudError(f"fetching content failed: {exc}") from exc
        return gzip.decompress(resp.content).decode("utf-8", errors="replace")

    def get_transcript(self, file_id: str) -> list[dict]:
        """List of ``{start_s, text}`` segments — structured for the UI;
        the MCP tool formats these into ``[Ns] text`` lines itself."""
        link = self._content_link(file_id, "transaction")
        if not link:
            raise PlaudError(
                "No transcript available for this recording yet (still "
                "processing, or it was never transcribed).", status=404,
            )
        segments = json.loads(self._fetch_gz_text(link))
        return [{"start_s": s["start_time"] // 1000, "text": s["content"]} for s in segments]

    def get_summary(self, file_id: str) -> str:
        link = self._content_link(file_id, "auto_sum_note")
        if not link:
            raise PlaudError("No summary available for this recording yet.", status=404)
        return self._fetch_gz_text(link)

    def audio_stream(self, file_id: str):
        """Returns an ``httpx.Response`` streamed against
        ``/file/download/{id}`` — NOT presigned S3, a direct authenticated
        byte stream (unlike transcript/summary). Caller (routes.py) is
        responsible for closing it. Raises PlaudError before ever handing
        back a non-audio (e.g. an error page) body as media — see the
        `download a 401 body and call it media` failure mode this guards
        against."""
        client = httpx.Client(timeout=60.0)
        try:
            req = client.build_request(
                "GET", f"{API_BASE}/file/download/{file_id}",
                headers=self._headers(accept_json=False),
            )
            resp = client.send(req, stream=True)
        except httpx.HTTPError as exc:
            client.close()
            raise PlaudError(f"audio download failed: {exc}") from exc
        if resp.status_code == 401:
            resp.close()
            client.close()
            raise PlaudError("Plaud API returned 401 Unauthorized — token expired.",
                             status=401, expired=True)
        if resp.status_code >= 400:
            resp.close()
            client.close()
            raise PlaudError(f"audio download failed: HTTP {resp.status_code}", status=resp.status_code)
        content_type = resp.headers.get("content-type", "")
        if "audio" not in content_type and "octet-stream" not in content_type and "video" not in content_type:
            # Plaud has never been observed to answer 200 with a non-audio
            # body here, but "trust the status code alone" is exactly the
            # bug this module exists to avoid — refuse rather than let the
            # browser <audio> tag try to play an HTML/JSON error page.
            resp.close()
            client.close()
            raise PlaudError(
                f"Plaud returned an unexpected content-type for audio: {content_type!r}",
                status=502,
            )
        return resp, client

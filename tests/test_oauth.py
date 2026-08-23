"""Unit tests for plaud_app/oauth.py — PKCE, the pending-state handshake,
token exchange/refresh, and the revoked-vs-transient-failure distinction
that decides whether stored tokens get cleared.

Run: .venv/aw/bin/python -m pytest tests/test_oauth.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_routes import FakeCtx  # noqa: E402

import plaud_app.oauth as oauth  # noqa: E402


def _configured_ctx(tmp_path) -> FakeCtx:
    ctx = FakeCtx(package_dir=str(tmp_path))
    ctx.config.update({
        "plaud_client_id": "client123",
        "plaud_oauth_authorize_url": "https://oauth.example.com/authorize",
        "plaud_oauth_token_url": "https://oauth.example.com/token",
    })
    ctx.secrets.write("plaud_client_secret", "secretXYZ")
    return ctx


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or json.dumps(json_body)

    def json(self):
        return self._json_body


def test_pkce_pair_challenge_matches_verifier():
    verifier, challenge = oauth.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert 43 <= len(verifier) <= 128


def test_missing_config_lists_every_absent_field(tmp_path):
    ctx = FakeCtx(package_dir=str(tmp_path))
    assert set(oauth.missing_config(ctx)) == {
        "plaud_client_id", "plaud_client_secret",
        "plaud_oauth_authorize_url", "plaud_oauth_token_url",
    }
    assert oauth.is_configured(ctx) is False


def test_missing_config_empty_once_all_four_are_set(tmp_path):
    ctx = _configured_ctx(tmp_path)
    assert oauth.missing_config(ctx) == []
    assert oauth.is_configured(ctx) is True


def test_start_authorization_raises_when_not_configured(tmp_path):
    ctx = FakeCtx(package_dir=str(tmp_path))
    try:
        oauth.start_authorization(ctx)
        assert False, "expected OAuthError"
    except oauth.OAuthError as exc:
        assert "missing" in str(exc)


def test_start_authorization_raises_without_a_published_workspace_url(tmp_path, monkeypatch):
    monkeypatch.delenv("AW_WORKSPACE_API_URL", raising=False)
    ctx = _configured_ctx(tmp_path)
    try:
        oauth.start_authorization(ctx)
        assert False, "expected OAuthError"
    except oauth.OAuthError as exc:
        assert "public URL" in str(exc)


def test_start_authorization_builds_pkce_url_and_stashes_pending_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    ctx = _configured_ctx(tmp_path)
    url = oauth.start_authorization(ctx)

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.scheme + "://" + parsed.netloc + parsed.path == "https://oauth.example.com/authorize"
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["client123"]
    assert qs["redirect_uri"] == ["https://api.testws.workspace.example.com/api/apps/plaud/oauth/callback"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "code_challenge" in qs and "state" in qs

    pending = json.loads(ctx.secrets.read(oauth.PENDING_SECRET_KEY))
    assert pending["state"] == qs["state"][0]
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(pending["code_verifier"].encode()).digest()
    ).rstrip(b"=").decode()
    assert expected_challenge == qs["code_challenge"][0]


def test_handle_callback_rejects_state_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    ctx = _configured_ctx(tmp_path)
    oauth.start_authorization(ctx)
    try:
        oauth.handle_callback(ctx, code="abc", state="not-the-real-state")
        assert False, "expected OAuthError"
    except oauth.OAuthError as exc:
        assert "State mismatch" in str(exc)


def test_handle_callback_surfaces_user_denial(tmp_path):
    ctx = _configured_ctx(tmp_path)
    try:
        oauth.handle_callback(ctx, code=None, state=None,
                              error="access_denied", error_description="user said no")
        assert False, "expected OAuthError"
    except oauth.OAuthError as exc:
        assert "user said no" in str(exc)


def test_handle_callback_exchanges_code_and_stores_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    ctx = _configured_ctx(tmp_path)
    url = oauth.start_authorization(ctx)
    state = parse_qs(urlparse(url).query)["state"][0]

    captured = {}

    def _fake_post(url, *, data, headers, timeout):
        captured.update(data)
        return _FakeResponse(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600})

    monkeypatch.setattr(oauth.httpx, "post", _fake_post)
    oauth.handle_callback(ctx, code="the-code", state=state)

    assert captured["grant_type"] == "authorization_code"
    assert captured["code"] == "the-code"
    assert captured["client_id"] == "client123"
    assert captured["client_secret"] == "secretXYZ"
    assert "code_verifier" in captured

    assert oauth.is_connected(ctx) is True
    token, error = oauth.get_valid_access_token(ctx)
    assert token == "AT1"
    assert error is None
    # one-shot: the pending state is gone even after a successful exchange
    assert ctx.secrets.read(oauth.PENDING_SECRET_KEY) is None


def test_handle_callback_treats_http_200_with_negative_body_status_as_error(tmp_path, monkeypatch):
    # Same regression client.py guards against for api.plaud.ai — a new
    # token endpoint could just as easily answer HTTP 200 with the real
    # error in the body.
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    ctx = _configured_ctx(tmp_path)
    url = oauth.start_authorization(ctx)
    state = parse_qs(urlparse(url).query)["state"][0]

    monkeypatch.setattr(
        oauth.httpx, "post",
        lambda *a, **k: _FakeResponse(200, {"status": -419, "msg": "workspace token expired"}),
    )
    try:
        oauth.handle_callback(ctx, code="the-code", state=state)
        assert False, "expected OAuthError"
    except oauth.OAuthError as exc:
        assert exc.revoked is True
    assert oauth.is_connected(ctx) is False


def test_get_valid_access_token_never_connected_returns_none_none(tmp_path):
    ctx = FakeCtx(package_dir=str(tmp_path))
    token, error = oauth.get_valid_access_token(ctx)
    assert token is None
    assert error is None


def test_get_valid_access_token_refreshes_when_near_expiry(tmp_path, monkeypatch):
    ctx = _configured_ctx(tmp_path)
    oauth._write_tokens(ctx, oauth.OAuthTokens(
        access_token="STALE", refresh_token="RT1", expires_at=time.time() + 5,
    ))
    monkeypatch.setattr(
        oauth.httpx, "post",
        lambda *a, **k: _FakeResponse(200, {"access_token": "FRESH", "expires_in": 3600}),
    )
    token, error = oauth.get_valid_access_token(ctx)
    assert token == "FRESH"
    assert error is None
    stored = json.loads(ctx.secrets.read(oauth.TOKENS_SECRET_KEY))
    assert stored["access_token"] == "FRESH"
    # provider omitted refresh_token on this refresh response -> old one carried forward
    assert stored["refresh_token"] == "RT1"


def test_get_valid_access_token_transient_refresh_failure_keeps_tokens(tmp_path, monkeypatch):
    ctx = _configured_ctx(tmp_path)
    oauth._write_tokens(ctx, oauth.OAuthTokens(
        access_token="STALE", refresh_token="RT1", expires_at=time.time() - 10,
    ))
    monkeypatch.setattr(
        oauth.httpx, "post",
        lambda *a, **k: _FakeResponse(500, {"error": "server_error"}),
    )
    token, error = oauth.get_valid_access_token(ctx)
    assert token is None
    assert error is not None
    # not cleared — a transient failure should be retryable, not a forced disconnect
    assert oauth.is_connected(ctx) is True


def test_get_valid_access_token_revoked_refresh_clears_tokens(tmp_path, monkeypatch):
    ctx = _configured_ctx(tmp_path)
    oauth._write_tokens(ctx, oauth.OAuthTokens(
        access_token="STALE", refresh_token="RT1", expires_at=time.time() - 10,
    ))
    monkeypatch.setattr(
        oauth.httpx, "post",
        lambda *a, **k: _FakeResponse(400, {"error": "invalid_grant"}),
    )
    token, error = oauth.get_valid_access_token(ctx)
    assert token is None
    assert error is None  # collapses to "never connected", not a stuck error state
    assert oauth.is_connected(ctx) is False


def test_make_token_getter_falls_back_to_pasted_bearer_when_never_connected(tmp_path):
    ctx = FakeCtx(package_dir=str(tmp_path))
    getter = oauth.make_token_getter(ctx, lambda: "pasted-bearer-token")
    assert getter() == "pasted-bearer-token"


def test_make_token_getter_does_not_fall_back_when_oauth_connected_but_broken(tmp_path, monkeypatch):
    ctx = _configured_ctx(tmp_path)
    oauth._write_tokens(ctx, oauth.OAuthTokens(
        access_token="STALE", refresh_token="RT1", expires_at=time.time() - 10,
    ))
    monkeypatch.setattr(
        oauth.httpx, "post",
        lambda *a, **k: _FakeResponse(500, {"error": "server_error"}),
    )
    getter = oauth.make_token_getter(ctx, lambda: "pasted-bearer-token")
    assert getter() is None


def test_disconnect_clears_tokens_and_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    ctx = _configured_ctx(tmp_path)
    oauth.start_authorization(ctx)
    oauth._write_tokens(ctx, oauth.OAuthTokens(access_token="AT1", refresh_token="RT1", expires_at=None))
    oauth.disconnect(ctx)
    assert oauth.is_connected(ctx) is False
    assert ctx.secrets.read(oauth.PENDING_SECRET_KEY) is None

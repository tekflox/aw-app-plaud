"""Unit tests for plaud_app/client.py that don't need a running FastAPI app —
JWT-exp parsing and the no-token short-circuit (no network call needed to
prove these).

Run: .venv/aw/bin/python -m pytest tests/test_client.py
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import plaud_app.client as client_module
from plaud_app.client import (  # noqa: E402
    PlaudClient,
    PlaudError,
    decode_token_exp,
    format_expiry_text,
    normalize_pasted_token,
)


class _FakeResponse:
    """Stand-in for httpx.Response — just enough surface for _get_json."""

    def __init__(self, status_code: int, json_body: dict, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text or json.dumps(json_body)

    def json(self):
        return self._json_body


def _fake_jwt(exp: float | None) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_obj = {"exp": exp} if exp is not None else {}
    payload = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def test_decode_token_exp_reads_claim():
    exp = time.time() + 3600
    assert decode_token_exp(_fake_jwt(exp)) == exp


def test_decode_token_exp_handles_non_jwt():
    assert decode_token_exp("not-a-jwt") is None
    assert decode_token_exp("") is None


def test_decode_token_exp_handles_jwt_without_exp():
    assert decode_token_exp(_fake_jwt(None)) is None


def test_status_without_token_makes_no_network_call():
    client = PlaudClient(lambda: None)
    st = client.status()
    assert st.configured is False
    assert st.valid is None
    assert st.expired is False


def test_status_with_blank_token_treated_as_unconfigured():
    client = PlaudClient(lambda: "   ")
    st = client.status()
    assert st.configured is False


def test_get_json_treats_http_200_with_negative_body_status_as_expired(monkeypatch):
    # Plaud answers HTTP 200 for a dead session — the real error is in the
    # JSON body (observed: {"status": -419, "msg": "workspace token expired"}).
    monkeypatch.setattr(
        client_module.httpx, "get",
        lambda *a, **k: _FakeResponse(200, {"status": -419, "msg": "workspace token expired"}),
    )
    client = PlaudClient(lambda: _fake_jwt(time.time() + 3600))
    try:
        client._get_json("/user/me")
        assert False, "expected PlaudError"
    except PlaudError as exc:
        assert exc.expired is True
        assert exc.status == 401


def test_get_json_treats_other_negative_body_status_as_error_not_success(monkeypatch):
    monkeypatch.setattr(
        client_module.httpx, "get",
        lambda *a, **k: _FakeResponse(200, {"status": -1, "msg": "something else broke"}),
    )
    client = PlaudClient(lambda: _fake_jwt(time.time() + 3600))
    try:
        client._get_json("/user/me")
        assert False, "expected PlaudError"
    except PlaudError as exc:
        assert exc.expired is False


def test_status_never_reports_valid_when_token_exp_claim_already_passed(monkeypatch):
    # Even if the HTTP call "succeeds" with a normal-looking body, a token
    # whose own exp claim is in the past must never be reported as valid.
    monkeypatch.setattr(
        client_module.httpx, "get",
        lambda *a, **k: _FakeResponse(200, {"data_user": {"email": "a@b.com"}}),
    )
    expired_token = _fake_jwt(time.time() - 3600)
    client = PlaudClient(lambda: expired_token)
    st = client.status()
    assert st.valid is False
    assert st.expired is True


def test_status_reports_valid_for_a_genuinely_fresh_token(monkeypatch):
    monkeypatch.setattr(
        client_module.httpx, "get",
        lambda *a, **k: _FakeResponse(200, {"data_user": {"email": "a@b.com"}}),
    )
    client = PlaudClient(lambda: _fake_jwt(time.time() + 3600))
    st = client.status()
    assert st.valid is True
    assert st.expired is False
    assert st.email == "a@b.com"


def test_normalize_pasted_token_strips_whitespace():
    assert normalize_pasted_token("  eyJ.abc.def  ") == "eyJ.abc.def"


def test_normalize_pasted_token_strips_bearer_prefix():
    assert normalize_pasted_token("Bearer eyJ.abc.def") == "eyJ.abc.def"
    assert normalize_pasted_token("bearer   eyJ.abc.def") == "eyJ.abc.def"


def test_normalize_pasted_token_strips_authorization_header_line():
    assert normalize_pasted_token("authorization: eyJ.abc.def") == "eyJ.abc.def"
    assert normalize_pasted_token("Authorization : Bearer eyJ.abc.def") == "eyJ.abc.def"


def test_normalize_pasted_token_handles_bare_token_unchanged():
    assert normalize_pasted_token("eyJ.abc.def") == "eyJ.abc.def"


def test_normalize_pasted_token_handles_none_and_empty():
    assert normalize_pasted_token(None) == ""
    assert normalize_pasted_token("   ") == ""


def test_format_expiry_text_none_when_no_claim():
    assert format_expiry_text(None) is None


def test_format_expiry_text_future_hours():
    assert format_expiry_text(time.time() + 3600 * 10) == "expires in 10h"


def test_format_expiry_text_future_minutes():
    text = format_expiry_text(time.time() + 300)
    assert text.startswith("expires in") and text.endswith("m")


def test_format_expiry_text_expired_days_ago():
    assert format_expiry_text(time.time() - 86400 * 3) == "expired 3d ago"


def test_format_expiry_text_expired_hours_ago():
    assert format_expiry_text(time.time() - 3600 * 5) == "expired 5h ago"


def test_format_expiry_text_just_expired():
    assert format_expiry_text(time.time() - 1) == "expired"

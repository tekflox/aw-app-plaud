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

from plaud_app.client import PlaudClient, decode_token_exp  # noqa: E402


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

"""TestClient coverage for plaud_app/routes.py's build_routes(ctx) — no
framework runtime needed, just a minimal fake ctx (secrets facade +
package_dir), same pattern as aw-app-notion's tests/test_routes.py.

Run: .venv/aw/bin/python -m pytest tests/test_routes.py
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plaud_app import mcp_config, oauth, routes  # noqa: E402
from plaud_app.client import PlaudClient, PlaudError  # noqa: E402


class FakeSecrets:
    def __init__(self):
        self.store: dict[str, str] = {}

    def read(self, key):
        return self.store.get(key)

    def write(self, key, value):
        self.store[key] = value
        return {"key": key, "written": True}

    def delete(self, key):
        removed = key in self.store
        self.store.pop(key, None)
        return {"key": key, "deleted": removed}

    def keys(self):
        return list(self.store)


class FakeCtx:
    def __init__(self, package_dir: str):
        self.secrets = FakeSecrets()
        self.config = {}
        self.package_dir = package_dir


def _client(tmp_path):
    ctx = FakeCtx(package_dir=str(tmp_path))
    return TestClient(routes.build_routes(ctx)), ctx


def _fake_jwt(exp: float | None) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_obj = {"exp": exp} if exp is not None else {}
    payload = base64.urlsafe_b64encode(json.dumps(payload_obj).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


def test_status_without_token_makes_no_network_call():
    # `configured` is True here even with zero tokens: OAuth's prerequisites
    # (plaud_client_id etc.) are also missing, and that IS something the
    # settings panel needs to say ("OAuth not configured — missing: ...")
    # rather than the generic "not connected" text — see oauth_missing.
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["logged_in"] is False
        assert body["expired"] is False
        assert body["mcp_server_enabled"] is False
        assert body["expires_in_text"] is None
        assert body["auth_method"] is None
        assert body["oauth_configured"] is False
        assert set(body["oauth_missing"]) == {
            "plaud_client_id", "plaud_client_secret",
            "plaud_oauth_authorize_url", "plaud_oauth_token_url",
        }
        assert "missing" in body["error"]


def test_status_reports_plain_disconnected_once_oauth_prereqs_are_all_set():
    # The narrow "click Connect below" state — distinct from "not
    # configured" — only once every OAuth prerequisite is actually present
    # and the user simply hasn't connected yet.
    with tempfile.TemporaryDirectory() as tmp:
        client, ctx = _client(Path(tmp))
        ctx.config.update({
            "plaud_client_id": "id123",
            "plaud_oauth_authorize_url": "https://example.com/authorize",
            "plaud_oauth_token_url": "https://example.com/token",
        })
        ctx.secrets.write("plaud_client_secret", "secret123")
        resp = client.get("/status")
        body = resp.json()
        assert body["oauth_configured"] is True
        assert body["configured"] is False
        assert body["error"] is None


def test_save_settings_writes_secret_and_mcp_json():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, ctx = _client(tmp_path)

        resp = client.post("/settings", json={"plaud_bearer_token": "eyTest.token.here"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["logged_in"] is True
        assert body["mcp_server_enabled"] is True
        assert ctx.secrets.read("plaud_bearer_token") == "eyTest.token.here"

        mcp_json_path = tmp_path / "mcp.json"
        assert mcp_json_path.is_file()
        doc = json.loads(mcp_json_path.read_text())
        assert doc["mcpServers"]["aw-plaud"]["type"] == "http"
        assert doc["mcpServers"]["aw-plaud"]["url"].endswith("/api/apps/plaud/mcp")


def test_save_settings_rejects_empty_token():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.post("/settings", json={"plaud_bearer_token": "  "})
        assert resp.status_code == 400
        assert "error" in resp.json()


def test_save_settings_normalizes_bearer_prefix_and_whitespace():
    with tempfile.TemporaryDirectory() as tmp:
        client, ctx = _client(Path(tmp))
        resp = client.post("/settings", json={"plaud_bearer_token": "  Bearer eyTest.token.here  "})
        assert resp.status_code == 200
        assert ctx.secrets.read("plaud_bearer_token") == "eyTest.token.here"


def test_save_settings_normalizes_a_whole_pasted_header_line():
    with tempfile.TemporaryDirectory() as tmp:
        client, ctx = _client(Path(tmp))
        resp = client.post("/settings", json={"plaud_bearer_token": "authorization: Bearer eyTest.token.here"})
        assert resp.status_code == 200
        assert ctx.secrets.read("plaud_bearer_token") == "eyTest.token.here"


def test_save_settings_rejects_a_header_line_with_nothing_after_the_colon():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.post("/settings", json={"plaud_bearer_token": "authorization:  "})
        assert resp.status_code == 400


def test_status_reports_expiry_text_after_saving_an_expiring_token(monkeypatch):
    # status() calls out to Plaud (client.status() -> /user/me) to check
    # validity; expires_in_text only needs the token's own exp claim, which
    # is computed before that call and survives it failing — mock the call
    # itself to failure so this test makes no real network request.
    def _raise(self, path, *, timeout=20.0):
        raise PlaudError("network unavailable in test")
    monkeypatch.setattr(PlaudClient, "_get_json", _raise)

    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        token = _fake_jwt(time.time() + 3600 * 10)
        client.post("/settings", json={"plaud_bearer_token": token})
        resp = client.get("/status")
        assert resp.json()["expires_in_text"] == "expires in 10h"


def test_logout_clears_secret_and_disables_mcp_server():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, ctx = _client(tmp_path)
        client.post("/settings", json={"plaud_bearer_token": "eyTest.token.here"})

        resp = client.post("/logout")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "logged_in": False, "configured": False}
        assert ctx.secrets.read("plaud_bearer_token") is None

        doc = json.loads((tmp_path / "mcp.json").read_text())
        assert doc["mcpServers"] == {}


def test_logout_also_clears_an_active_oauth_connection():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, ctx = _client(tmp_path)
        oauth.disconnect(ctx)  # no-op, just importing for the write below
        oauth._write_tokens(ctx, oauth.OAuthTokens(access_token="AT1", refresh_token="RT1", expires_at=None))

        resp = client.post("/logout")
        assert resp.status_code == 200
        assert oauth.is_connected(ctx) is False


def test_oauth_start_400s_with_a_clear_message_when_not_configured():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/oauth/start", follow_redirects=False)
        assert resp.status_code == 400
        assert "missing" in resp.text


def test_oauth_start_redirects_to_the_authorize_url_when_configured(monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    with tempfile.TemporaryDirectory() as tmp:
        client, ctx = _client(Path(tmp))
        ctx.config.update({
            "plaud_client_id": "id123",
            "plaud_oauth_authorize_url": "https://oauth.example.com/authorize",
            "plaud_oauth_token_url": "https://oauth.example.com/token",
        })
        ctx.secrets.write("plaud_client_secret", "secret123")

        resp = client.get("/oauth/start", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://oauth.example.com/authorize?")


def test_oauth_callback_completes_the_flow_and_regenerates_mcp_json(monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_URL", "https://api.testws.workspace.example.com")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, ctx = _client(tmp_path)
        ctx.config.update({
            "plaud_client_id": "id123",
            "plaud_oauth_authorize_url": "https://oauth.example.com/authorize",
            "plaud_oauth_token_url": "https://oauth.example.com/token",
        })
        ctx.secrets.write("plaud_client_secret", "secret123")

        start_resp = client.get("/oauth/start", follow_redirects=False)
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(start_resp.headers["location"]).query)["state"][0]

        monkeypatch.setattr(
            oauth.httpx, "post",
            lambda *a, **k: type("R", (), {
                "status_code": 200,
                "json": lambda self: {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600},
                "text": "",
            })(),
        )
        resp = client.get(f"/oauth/callback?code=abc&state={state}")
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert oauth.is_connected(ctx) is True

        doc = json.loads((tmp_path / "mcp.json").read_text())
        assert doc["mcpServers"]["aw-plaud"]["type"] == "http"


def test_oauth_callback_shows_the_error_when_state_is_wrong():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/oauth/callback?code=abc&state=bogus")
        assert resp.status_code == 400
        assert "Could not connect" in resp.text


def test_status_reports_oauth_tracked_expiry_for_an_opaque_access_token(monkeypatch):
    # An OAuth access token has no reason to be a JWT — status() must not
    # rely on decode_token_exp finding a claim that isn't there.
    def _raise(self, path, *, timeout=20.0):
        raise PlaudError("network unavailable in test")
    monkeypatch.setattr(PlaudClient, "_get_json", _raise)

    with tempfile.TemporaryDirectory() as tmp:
        client, ctx = _client(Path(tmp))
        expires_at = time.time() + 3600 * 10
        oauth._write_tokens(ctx, oauth.OAuthTokens(
            access_token="opaque-not-a-jwt-at-all", refresh_token="RT1", expires_at=expires_at,
        ))
        resp = client.get("/status")
        body = resp.json()
        assert body["auth_method"] == "oauth"
        assert body["expires_at"] == expires_at
        assert body["expires_in_text"] == "expires in 10h"


def test_oauth_disconnect_clears_tokens_and_mcp_json():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, ctx = _client(tmp_path)
        oauth._write_tokens(ctx, oauth.OAuthTokens(access_token="AT1", refresh_token="RT1", expires_at=None))

        resp = client.post("/oauth/disconnect")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert oauth.is_connected(ctx) is False
        doc = json.loads((tmp_path / "mcp.json").read_text())
        assert doc["mcpServers"] == {}


def test_mcp_json_endpoint_mirrors_disk_state():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        client, _ctx = _client(tmp_path)
        client.post("/settings", json={"plaud_bearer_token": "eyTest.token.here"})

        resp = client.get("/mcp.json")
        assert resp.json() == json.loads((tmp_path / "mcp.json").read_text())


def test_build_mcp_servers_empty_without_token():
    assert mcp_config.build_mcp_servers(None) == {}
    assert mcp_config.build_mcp_servers("") == {}


def test_build_mcp_servers_shape_with_token():
    servers = mcp_config.build_mcp_servers("ey_xyz", port=9030)
    assert set(servers) == {"aw-plaud"}
    assert servers["aw-plaud"]["type"] == "http"
    assert servers["aw-plaud"]["url"].endswith(":9030/api/apps/plaud/mcp")


def test_recordings_endpoint_uses_client(monkeypatch):
    monkeypatch.setattr(
        PlaudClient, "list_recordings",
        lambda self, limit=20, skip=0: [
            {"id": "abc", "filename": "Meeting.m4a", "duration_s": 120,
             "start_time_ms": 1755500000000, "is_transcribed": True, "has_summary": False},
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/recordings")
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["id"] == "abc"
        assert body[0]["is_transcribed"] is True


def test_recordings_endpoint_surfaces_expired_token_as_401(monkeypatch):
    from plaud_app.client import PlaudError

    def _raise(self, limit=20, skip=0):
        raise PlaudError("Plaud API returned 401 Unauthorized", status=401, expired=True)

    monkeypatch.setattr(PlaudClient, "list_recordings", _raise)
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/recordings")
        assert resp.status_code == 401
        body = resp.json()
        assert body["ok"] is False
        assert body["expired"] is True


def test_transcript_endpoint_shapes_segments(monkeypatch):
    monkeypatch.setattr(
        PlaudClient, "get_transcript",
        lambda self, file_id: [{"start_s": 0, "text": "hello"}, {"start_s": 3, "text": "world"}],
    )
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/recordings/abc/transcript")
        assert resp.status_code == 200
        assert resp.json()["segments"] == [
            {"start_s": 0, "text": "hello"}, {"start_s": 3, "text": "world"},
        ]

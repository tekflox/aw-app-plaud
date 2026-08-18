"""TestClient coverage for plaud_app/routes.py's build_routes(ctx) — no
framework runtime needed, just a minimal fake ctx (secrets facade +
package_dir), same pattern as aw-app-notion's tests/test_routes.py.

Run: .venv/aw/bin/python -m pytest tests/test_routes.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plaud_app import mcp_config, routes  # noqa: E402
from plaud_app.client import PlaudClient  # noqa: E402


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


def test_status_without_token_makes_no_network_call():
    with tempfile.TemporaryDirectory() as tmp:
        client, _ctx = _client(Path(tmp))
        resp = client.get("/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["logged_in"] is False
        assert body["expired"] is False
        assert body["mcp_server_enabled"] is False


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

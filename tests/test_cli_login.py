"""Unit tests for plaud_app/cli_login.py — output-line parsing, the
attempt lifecycle, the paste-and-complete step, and refresh. Never spawns a
real `npx` process (subprocess.Popen is monkeypatched) — the actual
subprocess mechanics (detection-gate trick, isolated HOME) were verified by
hand against the real CLI, see the module docstring and this delivery's
Kanban report.

Run: python3 -m pytest tests/test_cli_login.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_routes import FakeCtx  # noqa: E402

import plaud_app.cli_login as cli_login  # noqa: E402


# ── LoginAttempt.observe_line ────────────────────────────────────────────

def test_observe_line_extracts_authorize_url_and_moves_to_waiting():
    attempt = cli_login.LoginAttempt()
    attempt.observe_line("Detected AI clients:")
    assert attempt.phase == "starting"
    attempt.observe_line(
        "  https://web.plaud.ai/platform/oauth?client_id=abc&redirect_uri="
        "http%3A%2F%2Flocalhost%3A8199%2Fauth%2Fcallback&state=xyz"
    )
    assert attempt.authorize_url == (
        "https://web.plaud.ai/platform/oauth?client_id=abc&redirect_uri="
        "http%3A%2F%2Flocalhost%3A8199%2Fauth%2Fcallback&state=xyz"
    )
    assert attempt.phase == "waiting_for_browser"


def test_observe_line_detects_timeout():
    attempt = cli_login.LoginAttempt()
    attempt.phase = "waiting_for_browser"
    attempt.observe_line("  ✗ timed out after 2 minutes — you can retry with `plaud-mcp install --yes`")
    assert attempt.phase == "timed_out"


def test_observe_line_detects_success():
    attempt = cli_login.LoginAttempt()
    attempt.phase = "waiting_for_browser"
    attempt.observe_line("  ✓ authenticated as fred@example.com.")
    assert attempt.phase == "connected"


def test_observe_line_detects_already_signed_in():
    attempt = cli_login.LoginAttempt()
    attempt.phase = "waiting_for_browser"
    attempt.observe_line("  already signed in as fred@example.com — skipping OAuth.")
    assert attempt.phase == "connected"


def test_observe_line_detects_failure_and_captures_message():
    attempt = cli_login.LoginAttempt()
    attempt.phase = "waiting_for_browser"
    attempt.observe_line("  ✗ failed: token exchange failed: 400 invalid_grant")
    assert attempt.phase == "error"
    assert attempt.error == "token exchange failed: 400 invalid_grant"


def test_per_client_install_failure_line_is_not_mistaken_for_the_login_outcome():
    # "→ Cursor... failed: ..." (a per-client install failure, printed on the
    # SAME line as the "→ Cursor... " prefix) must not be mistaken for the
    # login step's own "  ✗ failed: ..." line — different prefix character.
    attempt = cli_login.LoginAttempt()
    attempt.phase = "starting"
    attempt.observe_line("→ Cursor... failed: could not write config")
    assert attempt.phase == "starting"
    assert attempt.error is None


def test_snapshot_counts_down_and_flips_to_timed_out_after_the_window():
    attempt = cli_login.LoginAttempt()
    attempt.phase = "waiting_for_browser"
    attempt.authorize_url = "https://web.plaud.ai/platform/oauth?x=1"
    attempt.started_at = time.time() - (cli_login.LOGIN_WINDOW_S - 10)
    snap = attempt.snapshot()
    assert snap["phase"] == "waiting_for_browser"
    assert 0 < snap["seconds_remaining"] <= 10

    attempt.started_at = time.time() - (cli_login.LOGIN_WINDOW_S + 5)
    snap = attempt.snapshot()
    assert snap["phase"] == "timed_out"
    assert snap["seconds_remaining"] == 0


def test_mark_exit_only_overrides_an_unresolved_phase():
    attempt = cli_login.LoginAttempt()
    attempt.phase = "connected"
    attempt.mark_exit(0)
    assert attempt.phase == "connected"  # already resolved, not clobbered

    attempt2 = cli_login.LoginAttempt()
    attempt2.phase = "waiting_for_browser"
    attempt2.mark_exit(1)
    assert attempt2.phase == "error"
    assert "exited (1)" in attempt2.error


# ── CliLogin.start() — subprocess spawn, no real npx ─────────────────────

class _FakeProc:
    def __init__(self, lines=(), returncode=0, pid=999999):
        self.stdout = iter(lines)
        self.pid = pid
        self.returncode = returncode
        self._killed = False

    def poll(self):
        return self.returncode if self._killed else None

    def wait(self, timeout=None):
        self._killed = True
        return self.returncode


def test_start_isolates_home_and_npm_cache_and_preseeds_cursor_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        captured["cwd"] = kwargs["cwd"]
        return _FakeProc()

    monkeypatch.setattr(cli_login.subprocess, "Popen", fake_popen)
    mgr = cli_login.CliLogin(FakeCtx(package_dir=str(tmp_path)))
    mgr.start()

    home = tmp_path / "data" / "plaud" / "cli-home"
    assert captured["argv"] == ["npx", "-y", "@plaud-ai/mcp", "install", "--yes"]
    assert captured["env"]["HOME"] == str(home)
    assert captured["env"]["npm_config_cache"] == str(tmp_path / "data" / "plaud" / "npm-cache")
    assert (home / ".cursor").is_dir()


def test_start_kills_the_previous_attempt_before_spawning_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.setattr(cli_login.subprocess, "Popen", lambda *a, **k: _FakeProc())
    killed = []
    monkeypatch.setattr(cli_login, "_kill", lambda proc: killed.append(proc))

    mgr = cli_login.CliLogin(FakeCtx(package_dir=str(tmp_path)))
    mgr.start()
    first_attempt = mgr._attempt
    mgr.start()

    assert killed == [first_attempt.proc]


def test_kill_terminates_the_whole_process_group():
    # A real (tiny) subprocess this time — the one thing worth proving for
    # real is that `_kill` actually reaches a process, not just that it
    # doesn't crash on a fake one.
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    assert proc.poll() is None
    cli_login._kill(proc)
    assert proc.wait(timeout=5) is not None
    assert proc.poll() is not None


def test_status_reports_idle_before_any_attempt(tmp_path):
    mgr = cli_login.CliLogin(FakeCtx(package_dir=str(tmp_path)))
    assert mgr.status() == {"phase": "idle", "authorize_url": None, "seconds_remaining": 0, "error": None}


# ── CliLogin.complete() ───────────────────────────────────────────────────

def test_complete_without_an_attempt_in_progress_errors():
    mgr = cli_login.CliLogin(FakeCtx(package_dir="/tmp"))
    result = mgr.complete("http://localhost:8199/auth/callback?code=abc&state=xyz")
    assert result == {"ok": False, "error": "No connection attempt in progress — click Connect first."}


def test_complete_rejects_a_url_with_no_code_or_state(tmp_path):
    mgr = cli_login.CliLogin(FakeCtx(package_dir=str(tmp_path)))
    mgr._attempt = cli_login.LoginAttempt()
    mgr._attempt.phase = "waiting_for_browser"
    result = mgr.complete("http://localhost:8199/auth/callback?error_only=1")
    assert result["ok"] is False
    assert "code/state" in result["error"]


def test_complete_surfaces_a_denied_authorization():
    mgr = cli_login.CliLogin(FakeCtx(package_dir="/tmp"))
    mgr._attempt = cli_login.LoginAttempt()
    mgr._attempt.phase = "waiting_for_browser"
    result = mgr.complete("http://localhost:8199/auth/callback?error=access_denied&error_description=nope")
    assert result == {"ok": False, "error": "Plaud reported: nope"}


def test_complete_reports_when_the_cli_callback_server_is_unreachable(monkeypatch):
    mgr = cli_login.CliLogin(FakeCtx(package_dir="/tmp"))
    mgr._attempt = cli_login.LoginAttempt()
    mgr._attempt.phase = "waiting_for_browser"

    def _raise(*a, **k):
        raise cli_login.httpx.ConnectError("refused")

    monkeypatch.setattr(cli_login.httpx, "get", _raise)
    result = mgr.complete("http://localhost:8199/auth/callback?code=c1&state=s1")
    assert result["ok"] is False
    assert "callback server" in result["error"]


def test_complete_happy_path_copies_tokens_and_converts_ms_to_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
    ctx = FakeCtx(package_dir=str(tmp_path))
    mgr = cli_login.CliLogin(ctx)
    mgr._attempt = cli_login.LoginAttempt()
    mgr._attempt.phase = "waiting_for_browser"

    calls = {}

    def fake_get(url, params=None, timeout=None):
        calls["url"], calls["params"] = url, params
        mgr._attempt.phase = "connected"  # simulate the subprocess's reader thread landing this

        class _Resp:
            status_code = 200
        return _Resp()

    monkeypatch.setattr(cli_login.httpx, "get", fake_get)

    now_ms = time.time() * 1000
    tokens_path = cli_login._home_dir() / ".plaud" / "tokens-mcp.json"
    tokens_path.parent.mkdir(parents=True, exist_ok=True)
    tokens_path.write_text(json.dumps({
        "access_token": "AT1", "refresh_token": "RT1",
        "token_type": "Bearer", "expires_at": now_ms + 3600 * 1000,
    }))

    result = mgr.complete("http://localhost:8199/auth/callback?code=c1&state=s1")
    assert result == {"ok": True}
    assert calls["url"] == cli_login.CALLBACK_URL
    assert calls["params"] == {"code": "c1", "state": "s1"}

    stored = json.loads(ctx.secrets.read(cli_login.CLI_TOKENS_SECRET_KEY))
    assert stored["access_token"] == "AT1"
    assert stored["refresh_token"] == "RT1"
    # ms epoch (JS Date.now()-based) -> seconds epoch (this app's convention)
    assert abs(stored["expires_at"] - (now_ms / 1000.0 + 3600)) < 1


def test_complete_times_out_waiting_for_the_subprocess_to_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
    mgr = cli_login.CliLogin(FakeCtx(package_dir=str(tmp_path)))
    mgr._attempt = cli_login.LoginAttempt()
    mgr._attempt.phase = "waiting_for_browser"
    mgr._attempt.started_at = time.time()  # stays inside the 2-minute window

    monkeypatch.setattr(cli_login, "COMPLETE_WAIT_S", 0.2)

    class _Resp:
        status_code = 200
    monkeypatch.setattr(cli_login.httpx, "get", lambda *a, **k: _Resp())

    result = mgr.complete("http://localhost:8199/auth/callback?code=c1&state=s1")
    assert result["ok"] is False
    assert "did not confirm" in result["error"]


# ── token store: is_connected / token_expiry / disconnect / refresh ──────

def test_is_connected_false_when_nothing_stored():
    ctx = FakeCtx(package_dir="/tmp")
    assert cli_login.is_connected(ctx) is False
    assert cli_login.token_expiry(ctx) is None


def test_disconnect_clears_the_secret():
    ctx = FakeCtx(package_dir="/tmp")
    ctx.secrets.write(cli_login.CLI_TOKENS_SECRET_KEY, json.dumps({"access_token": "AT1"}))
    cli_login.disconnect(ctx)
    assert cli_login.is_connected(ctx) is False


def test_get_valid_access_token_returns_the_stored_token_when_far_from_expiry():
    ctx = FakeCtx(package_dir="/tmp")
    ctx.secrets.write(cli_login.CLI_TOKENS_SECRET_KEY, json.dumps({
        "access_token": "AT1", "refresh_token": "RT1", "expires_at": time.time() + 3600,
    }))
    token, error = cli_login.get_valid_access_token(ctx)
    assert token == "AT1"
    assert error is None


def test_get_valid_access_token_refreshes_near_expiry_with_no_client_credentials(monkeypatch):
    ctx = FakeCtx(package_dir="/tmp")
    ctx.secrets.write(cli_login.CLI_TOKENS_SECRET_KEY, json.dumps({
        "access_token": "STALE", "refresh_token": "RT1", "expires_at": time.time() + 5,
    }))

    captured = {}

    def fake_post(url, *, data, headers, timeout):
        captured["url"], captured["data"] = url, data

        class _Resp:
            status_code = 200
            def json(self):
                return {"access_token": "FRESH", "refresh_token": "RT2", "expires_in": 3600}
        return _Resp()

    monkeypatch.setattr(cli_login.httpx, "post", fake_post)
    token, error = cli_login.get_valid_access_token(ctx)
    assert token == "FRESH"
    assert error is None
    assert captured["url"] == cli_login.REFRESH_URL
    # no client_id/secret on refresh — confirmed against the CLI's own bundle
    assert captured["data"] == {"refresh_token": "RT1"}

    stored = json.loads(ctx.secrets.read(cli_login.CLI_TOKENS_SECRET_KEY))
    assert stored["access_token"] == "FRESH"
    assert stored["refresh_token"] == "RT2"


def test_get_valid_access_token_transient_refresh_failure_keeps_tokens(monkeypatch):
    ctx = FakeCtx(package_dir="/tmp")
    ctx.secrets.write(cli_login.CLI_TOKENS_SECRET_KEY, json.dumps({
        "access_token": "STALE", "refresh_token": "RT1", "expires_at": time.time() - 10,
    }))

    class _Resp:
        status_code = 500
        def json(self):
            return {"error": "server_error"}
    monkeypatch.setattr(cli_login.httpx, "post", lambda *a, **k: _Resp())

    token, error = cli_login.get_valid_access_token(ctx)
    assert token is None
    assert error is not None
    assert cli_login.is_connected(ctx) is True  # kept for a retry, not cleared


def test_get_valid_access_token_disconnects_when_there_is_no_refresh_token():
    ctx = FakeCtx(package_dir="/tmp")
    ctx.secrets.write(cli_login.CLI_TOKENS_SECRET_KEY, json.dumps({
        "access_token": "STALE", "refresh_token": None, "expires_at": time.time() - 10,
    }))
    token, error = cli_login.get_valid_access_token(ctx)
    assert token is None
    assert error is None
    assert cli_login.is_connected(ctx) is False

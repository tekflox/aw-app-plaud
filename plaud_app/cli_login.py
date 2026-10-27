"""Orchestrates the official ``@plaud-ai/mcp`` CLI's own OAuth login —
this app runs the real, first-party client, instead of trying to be one.

Why we can't just implement this OAuth flow ourselves (skip the subprocess
entirely): the CLI's own bundle (inspected 2026-08-24, `npm pack
@plaud-ai/mcp` + read the dist/*.js) shows its redirect_uri is hardcoded to
``http://localhost:8199/auth/callback`` for its own first-party
``client_id`` — Plaud's OAuth server accepts that exact redirect_uri for
that exact client_id and no other. We have no client_id of our own (that's
the *waitlist* path ``oauth.py`` is stuck on), and even if we reused the
CLI's public client_id, our public callback URL still isn't the registered
redirect_uri. Only a process that actually owns ``localhost:8199`` can
complete this — so we make the CLI run *here*, in this app's own container,
where ``localhost:8199`` is reachable by us even though it never was by an
external browser.

The four gotchas from the Kanban card, and how each is handled:

1. **The 2-minute window.** The CLI's own login step gives up after
   ``LOGIN_TIMEOUT_MS = 120000`` (confirmed in its bundle) and prints
   ``✗ timed out after 2 minutes``. Each attempt mints its own PKCE pair, so
   a stale authorize_url from a killed/expired attempt is never reusable —
   ``start()`` always kills whatever attempt is currently running (see
   ``_kill``) before spawning a fresh one, both for an explicit "restart"
   and implicitly by calling ``start()`` again.
2. **Where ``~/.plaud/`` lives.** The CLI writes its tokens to
   ``$HOME/.plaud/tokens-mcp.json`` (``homedir()`` in its bundle). We run it
   with ``HOME`` pointed at ``paths.data_dir()/cli-home`` — durable, and
   ours — so the token survives a container recreation instead of dying
   with whatever ephemeral ``$HOME`` an in-process app would otherwise run
   under.
3. **``install`` touches ``~/.claude``.** Same ``HOME`` override contains
   this too — every write lands under our isolated dir, never the real
   workspace ``~/.claude``. The subtlety: the installer only runs its login
   step at all if at least one *local* AI client is "detected" (its own
   ``needsLocalAuth`` gate — with zero detected clients it prints "Nothing
   to configure" and exits before ever starting OAuth). We deliberately do
   NOT fake "Claude Code" detected for this (its adapter shells out to a
   real ``claude mcp add --scope user``, which needs the ``claude`` binary
   on PATH in whatever process runs this app — not guaranteed, and it would
   also trigger a 7-skill copy). Instead ``start()`` pre-creates an empty
   ``.cursor/`` dir under the isolated HOME: the Cursor adapter's own
   detection (``existsSync(HOME/.cursor) || existsSync(HOME)``, from the
   CLI's bundle) is satisfied by the bare directory, and its "install" is a
   plain JSON file write (``~/.cursor/mcp.json``) with no external process
   and no skills copy — the least-effectful client that still satisfies the
   gate. Its write still lands under our isolated dir either way.
4. **npx/npm cache.** ``npm_config_cache`` points at our own
   ``paths.data_dir()/npm-cache`` — never the shared ``$HOME/.npm``, which
   is how a root-owned cache entry left by an unrelated process turns into
   an EACCES for every later install (hit for real in aw-app-whatsapp).

What this module does NOT do: reimplement the CLI's token *exchange*. The
subprocess's own bundled ``exchangeCode`` handles that once we GET its
callback server with ``code``/``state`` (see ``complete()``). We only
replicate its token *refresh* (``get_valid_access_token``), because we're
not running that subprocess continuously — the refresh endpoint and its
request shape come from the same bundle inspection (no client_id/secret
needed on refresh, confirmed by reading ``OAuth.refresh()``).

The resulting access_token is fed into the very same ``PlaudClient`` (see
``routes.py``'s combined token getter) that the pasted-bearer and own-OAuth
paths already feed — exactly how ``oauth.py`` already treats its own
OAuth-obtained tokens as interchangeable with a pasted bearer. This has
**not** been verified against a real, completed browser authorization (that
step needs a human) — if the CLI-obtained token turns out not to be valid
for ``api.plaud.ai``'s endpoints, ``client.status()`` will surface that as a
plain expired/401 the same way any other bad token would.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import httpx

from . import paths

log = logging.getLogger("aw_apps.plaud.cli_login")

CALLBACK_PORT = 8199
CALLBACK_URL = f"http://127.0.0.1:{CALLBACK_PORT}/auth/callback"
LOGIN_WINDOW_S = 120  # mirrors the CLI's own LOGIN_TIMEOUT_MS
PROC_TIMEOUT_S = 180  # npx resolve + the 120s login window + slack
COMPLETE_WAIT_S = 15  # how long we wait, after the callback GET, for the CLI to report an outcome

# Cross-worker state (AW_WORKSPACE_WORKERS=5, docs/standards/app-shared-state.md).
# One in-progress attempt is a single fact shared by every worker: a named
# lease decides which worker's subprocess is the real one, and a TTL'd kv
# record lets every OTHER worker answer status()/complete() for an attempt
# it did not start.
_LEASE_NAME = "cli-login"
_ATTEMPT_KV_NAME = "cli-login-attempt"
_KV_BRIDGE_TIMEOUT_S = 5.0

# Discovered from the CLI's own bundle (chunk-5NWKLF3V.js) — the real
# "Plaud OAuth API" endpoints, previously unpublished (see oauth.py's
# docstring). We only use the refresh one; the exchange endpoint is used by
# the CLI subprocess itself, never by us directly.
REFRESH_URL = "https://platform.plaud.ai/developer/api/oauth/third-party/access-token/refresh"
REFRESH_MARGIN_S = 60

CLI_TOKENS_SECRET_KEY = "plaud_cli_tokens"

_AUTHORIZE_URL_RE = re.compile(r"https://web\.plaud\.ai/platform/oauth\?\S+")


def _home_dir() -> Path:
    return paths.data_dir() / "cli-home"


def _npm_cache_dir() -> Path:
    return paths.data_dir() / "npm-cache"


def _tokens_path() -> Path:
    return _home_dir() / ".plaud" / "tokens-mcp.json"


def _public_snapshot(phase: str, authorize_url: str | None, error: str | None,
                      started_at: float) -> dict:
    """The countdown/timeout derivation ``LoginAttempt.snapshot()`` always
    did, factored out so a kv-mirrored record (a point-in-time dict, no
    ``LoginAttempt`` object behind it on the reading worker) can be turned
    into the exact same public shape."""
    remaining = 0
    if phase == "waiting_for_browser":
        remaining = max(0, int(LOGIN_WINDOW_S - (time.time() - started_at)))
        if remaining <= 0:
            phase = "timed_out"
    return {"phase": phase, "authorize_url": authorize_url, "seconds_remaining": remaining, "error": error}


class LoginAttempt:
    """One ``npx @plaud-ai/mcp install --yes`` run and what its stdout has
    told us so far. A fresh instance per attempt."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.started_at: float = 0.0
        self.authorize_url: str | None = None
        # starting -> waiting_for_browser -> connected | timed_out | error
        self.phase: str = "starting"
        self.error: str | None = None
        self._lock = threading.Lock()
        # Set by CliLogin.start() right after construction — fired (off the
        # lock) on every mutation below so the originating worker's CliLogin
        # can mirror the new fact into ctx.state.kv for every other worker.
        self.on_update: Callable[[], None] | None = None

    def observe_line(self, line: str) -> None:
        with self._lock:
            if self.authorize_url is None:
                m = _AUTHORIZE_URL_RE.search(line)
                if m:
                    self.authorize_url = m.group(0)
                    if self.phase == "starting":
                        self.phase = "waiting_for_browser"
            if "timed out after 2 minutes" in line:
                self.phase = "timed_out"
            elif "authenticated" in line and line.strip().startswith("✓"):
                self.phase = "connected"
            elif "already signed in" in line:
                self.phase = "connected"
            elif "failed:" in line and line.strip().startswith("✗"):
                self.phase = "error"
                self.error = line.split("failed:", 1)[1].strip()
        if self.on_update is not None:
            self.on_update()

    def mark_exit(self, returncode: int) -> None:
        with self._lock:
            if self.phase not in ("connected", "timed_out", "error"):
                self.phase = "error"
                self.error = f"plaud-mcp install exited ({returncode}) with no recognized outcome"
        if self.on_update is not None:
            self.on_update()

    def mark_killed(self, reason: str) -> None:
        with self._lock:
            if self.phase not in ("connected", "timed_out", "error"):
                self.phase = "error"
                self.error = reason
        if self.on_update is not None:
            self.on_update()

    def snapshot(self) -> dict:
        with self._lock:
            phase, authorize_url, error, started_at = self.phase, self.authorize_url, self.error, self.started_at
        return _public_snapshot(phase, authorize_url, error, started_at)

    def record(self) -> dict:
        """The raw fact snapshot mirrored to ``ctx.state.kv`` — distinct
        from ``snapshot()``'s derived public shape (seconds_remaining is
        recomputed from ``started_at`` wherever it's read, not stored) and
        including the subprocess ``pid`` (not the ``Popen`` object, which
        cannot cross a process boundary) so another worker can signal it."""
        with self._lock:
            return {
                "phase": self.phase,
                "authorize_url": self.authorize_url,
                "error": self.error,
                "started_at": self.started_at,
                "pid": self.proc.pid if self.proc is not None else None,
            }


def _kill(proc: subprocess.Popen | None) -> None:
    """Kill the whole process group, not just the ``npx`` pid: Node's
    ``child_process`` (npx -> node -> the login step's own HTTP server on
    :8199, and the ``claude mcp add`` it shells out to) stays in the same
    group under ``start_new_session=True``, and a restart that only kills
    the top pid leaves the old server holding the port — the exact
    ``EADDRINUSE`` the CLI's own bundle warns about."""
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_by_pid(pid: int) -> None:
    """Same whole-process-group kill as ``_kill``, for a subprocess this
    worker did not itself spawn — any worker may need to clear a stale
    attempt left by another one, by pid (from the kv snapshot), since there
    is no local ``Popen`` to signal or ``.wait()`` on here. The PID
    namespace is shared (one container), so sending the signal still
    reaches the real process; ``.wait()`` would raise ECHILD on a pid this
    process never forked, so this escalates on a short poll instead."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.5)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reader(proc: subprocess.Popen, attempt: LoginAttempt) -> None:
    try:
        for raw in proc.stdout:  # text mode, line-buffered
            attempt.observe_line(raw.rstrip("\n"))
    except Exception:
        log.exception("plaud cli login: reader thread crashed")
    finally:
        returncode = proc.wait()
        attempt.mark_exit(returncode)


def _watchdog(proc: subprocess.Popen, attempt: LoginAttempt) -> None:
    time.sleep(PROC_TIMEOUT_S)
    if proc.poll() is None:
        log.warning("plaud cli login: process still running after %ss — killing it", PROC_TIMEOUT_S)
        _kill(proc)
        attempt.mark_killed("the Plaud CLI process ran too long and was killed")


class CliLogin:
    """One instance lives for the app's lifetime (see ``routes.py``), one
    per worker process (``AW_WORKSPACE_WORKERS``). ``self._attempt`` is
    this worker's own in-memory handle on an attempt it originated —
    intentionally NOT persisted: on a workspace restart it resets to
    "idle", but ``ctx.secrets``' ``CLI_TOKENS_SECRET_KEY`` (the actual
    connection) survives that unaffected. Callers that need to know "are we
    really connected" should read ``is_connected(ctx)``, not this object's
    ``status()`` — the latter is only about an *in-progress* attempt.

    A ``POST /cli/login/status`` or ``/complete`` following a ``start()``
    almost always lands on a DIFFERENT worker (``self._attempt is None``
    there) — see docs/standards/app-shared-state.md. Ownership of the
    subprocess (only one worker may spawn it) is ``ctx.state.lease``,
    flock-backed; the attempt's phase/authorize_url/error/pid is mirrored to
    ``ctx.state.kv`` (TTL'd, Redis-backed) on every update so any worker can
    answer for it.
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._attempt: LoginAttempt | None = None
        # Captured at construction time, which always happens on the ASGI
        # event loop (build_routes(ctx) runs inside plugin.py's async
        # activate()) — the loop ctx.state's redis.asyncio client gets bound
        # to on first use. start()/complete() run on a plain thread
        # (asyncio.to_thread), and the subprocess reader/watchdog are plain
        # threading.Thread targets — none of them have a loop of their own,
        # so kv calls from them are bridged onto THIS loop via
        # run_coroutine_threadsafe rather than asyncio.run()'ing a throwaway
        # one, which would silently trip the breaker against a client bound
        # to a different loop. None in a context with no running loop (e.g.
        # a unit test that constructs CliLogin outside one) — the bridge
        # then degrades to a no-op/miss, same posture as Redis being down.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ---- ctx.state bridge (never called from the loop thread itself —
    # that would deadlock; see the loop capture note above) ---------------

    def _kv_put_sync(self, record: dict, ttl: float) -> None:
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            self.ctx.state.kv.put(_ATTEMPT_KV_NAME, record, ttl=ttl), self._loop)
        try:
            future.result(timeout=_KV_BRIDGE_TIMEOUT_S)
        except Exception:
            log.debug("plaud cli login: could not mirror the attempt via ctx.state.kv", exc_info=True)

    def _kv_get_sync(self) -> dict | None:
        if self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(
            self.ctx.state.kv.get(_ATTEMPT_KV_NAME), self._loop)
        try:
            return future.result(timeout=_KV_BRIDGE_TIMEOUT_S)
        except Exception:
            log.debug("plaud cli login: could not read the shared attempt snapshot", exc_info=True)
            return None

    def _mirror_attempt(self, attempt: LoginAttempt) -> None:
        """Called (off the originating worker's reader/watchdog threads, or
        from start() itself) on every ``LoginAttempt`` mutation. Releases
        the lease once the attempt is resolved — a worker that still held
        it after that would block every other worker's restart forever."""
        record = attempt.record()
        self._kv_put_sync(record, ttl=LOGIN_WINDOW_S + 30)
        if record["phase"] in ("connected", "timed_out", "error"):
            self.ctx.state.lease.release(_LEASE_NAME)

    def _kv_snapshot_or_idle(self) -> dict:
        record = self._kv_get_sync()
        if record is None:
            return {"phase": "idle", "authorize_url": None, "seconds_remaining": 0, "error": None}
        return _public_snapshot(record.get("phase", "starting"), record.get("authorize_url"),
                                 record.get("error"), record.get("started_at") or 0.0)

    def _current_snapshot(self) -> dict:
        """This worker's own attempt if it has one; otherwise whatever the
        shared kv record says. Sync — bridges like the methods above, so
        never call this from the loop thread itself."""
        if self._attempt is not None:
            return self._attempt.snapshot()
        return self._kv_snapshot_or_idle()

    def start(self) -> dict:
        claimed = self.ctx.state.lease.claim(_LEASE_NAME)
        if not claimed:
            # Another worker's attempt is live right now — report it rather
            # than racing a second `npx ... install` into the same
            # port-8199. Read from kv, not self._attempt: THIS worker's own
            # attempt (if any) is necessarily a stale, already-resolved one,
            # since it would still hold the lease otherwise.
            return self._kv_snapshot_or_idle()

        if self._attempt is not None:
            _kill(self._attempt.proc)
        else:
            # We just won a lease nobody held — if that's because a prior
            # attempt (on any worker, including this one in an earlier
            # cycle) released it on reaching a terminal phase, its process
            # can still be lingering (the watchdog fires up to
            # PROC_TIMEOUT_S - LOGIN_WINDOW_S later). Clear it by pid before
            # spawning a fresh one into the same port.
            prev = self._kv_get_sync()
            prev_pid = prev.get("pid") if prev else None
            if prev_pid:
                _kill_by_pid(prev_pid)

        home = _home_dir()
        (home / ".cursor").mkdir(parents=True, exist_ok=True)
        _npm_cache_dir().mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["HOME"] = str(home)
        env["npm_config_cache"] = str(_npm_cache_dir())

        attempt = LoginAttempt()
        attempt.started_at = time.time()
        attempt.on_update = lambda: self._mirror_attempt(attempt)
        try:
            proc = subprocess.Popen(
                ["npx", "-y", "@plaud-ai/mcp", "install", "--yes"],
                cwd=str(paths.data_dir()),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            attempt.phase = "error"
            attempt.error = f"could not start the Plaud CLI: {exc}"
            self._attempt = attempt
            self._mirror_attempt(attempt)
            return attempt.snapshot()

        attempt.proc = proc
        self._attempt = attempt
        self._mirror_attempt(attempt)  # visible cross-worker before the first stdout line lands
        threading.Thread(target=_reader, args=(proc, attempt), daemon=True).start()
        threading.Thread(target=_watchdog, args=(proc, attempt), daemon=True).start()
        return attempt.snapshot()

    async def status(self) -> dict:
        """Always called directly from the async route (never threaded),
        i.e. already on the loop ``ctx.state``'s client lives on — awaits
        directly instead of going through the sync bridge above, which
        would deadlock if used from this thread."""
        if self._attempt is not None:
            return self._attempt.snapshot()
        record = await self.ctx.state.kv.get(_ATTEMPT_KV_NAME)
        if record is None:
            return {"phase": "idle", "authorize_url": None, "seconds_remaining": 0, "error": None}
        return _public_snapshot(record.get("phase", "starting"), record.get("authorize_url"),
                                 record.get("error"), record.get("started_at") or 0.0)

    def complete(self, callback_url: str) -> dict:
        """The one step only a human can do: paste the URL the browser
        landed on (a connection-refused page — expected, see the panel
        copy) after authorizing. Extracts ``code``/``state`` and GETs the
        CLI's own callback server with them; the subprocess does the actual
        code exchange and writes the token file, this just waits for that
        to land and copies the result into our own secret store.

        Works the same whether this worker originated the attempt or not:
        ``_current_snapshot()`` reads the local object when present and the
        shared kv record otherwise. The callback GET below always reaches
        the subprocess (shared network namespace, confirmed live 2026-10-27)
        regardless of which worker issues it — only the snapshot lookup was
        ever worker-local."""
        snap = self._current_snapshot()
        if snap["phase"] not in ("waiting_for_browser", "timed_out"):
            return {"ok": False, "error": "No connection attempt in progress — click Connect first."}

        parsed = urlparse(callback_url.strip())
        qs = parse_qs(parsed.query)
        code = (qs.get("code") or [None])[0]
        state = (qs.get("state") or [None])[0]
        error = (qs.get("error") or [None])[0]
        if error:
            desc = (qs.get("error_description") or [error])[0]
            return {"ok": False, "error": f"Plaud reported: {desc}"}
        if not code or not state:
            return {
                "ok": False,
                "error": "That URL has no code/state in it — paste the exact address "
                         "from the browser's address bar after it failed to connect.",
            }

        try:
            resp = httpx.get(CALLBACK_URL, params={"code": code, "state": state}, timeout=15.0)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"could not reach the CLI's own callback server: {exc}"}
        if resp.status_code >= 500:
            return {
                "ok": False,
                "error": f"the CLI rejected the callback (HTTP {resp.status_code}) — "
                         "the link may have expired; click Connect to restart.",
            }

        # The GET only kicks off the exchange server-side (see the CLI's
        # own oauth-callback-server.js) — give the subprocess a moment to
        # finish it and print its outcome line. Only the ORIGINATING
        # worker's reader thread observes that line, so a non-originating
        # worker has to poll the shared kv record instead of a local object.
        deadline = time.time() + COMPLETE_WAIT_S
        snap = self._current_snapshot()
        while time.time() < deadline and snap["phase"] not in ("connected", "timed_out", "error"):
            time.sleep(0.5)
            snap = self._current_snapshot()
        if snap["phase"] != "connected":
            return {
                "ok": False,
                "error": snap["error"] or "the CLI did not confirm the connection in time — try again.",
            }

        tokens = self._load_tokens()
        if tokens is None:
            return {"ok": False, "error": "the CLI reported success but wrote no token file — please report this."}
        self._store_tokens(tokens)
        return {"ok": True}

    def _load_tokens(self) -> dict | None:
        try:
            return json.loads(_tokens_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _store_tokens(self, cli_tokens: dict) -> None:
        # The CLI's own token file uses `Date.now() + expires_in * 1000`
        # (JS epoch millis) — convert to the seconds-since-epoch this app's
        # other token stores (oauth.py) already use.
        expires_at_ms = cli_tokens.get("expires_at")
        self.ctx.secrets.write(CLI_TOKENS_SECRET_KEY, json.dumps({
            "access_token": cli_tokens["access_token"],
            "refresh_token": cli_tokens.get("refresh_token"),
            "expires_at": expires_at_ms / 1000.0 if expires_at_ms else None,
        }))


# ── token store — same shape/contract as oauth.py, so routes.py can treat
# both sources uniformly ─────────────────────────────────────────────────

def _read_tokens(ctx) -> dict | None:
    raw = ctx.secrets.read(CLI_TOKENS_SECRET_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if data.get("access_token") else None


def is_connected(ctx) -> bool:
    return _read_tokens(ctx) is not None


def token_expiry(ctx) -> float | None:
    data = _read_tokens(ctx)
    return data.get("expires_at") if data else None


def disconnect(ctx) -> None:
    ctx.secrets.delete(CLI_TOKENS_SECRET_KEY)


def _refresh(refresh_token: str) -> dict:
    """No client_id/secret on refresh — confirmed by reading the CLI
    bundle's own ``OAuth.refresh()``, unlike the initial exchange (which
    the subprocess does, never us) that sends Basic auth."""
    try:
        resp = httpx.post(
            REFRESH_URL,
            data={"refresh_token": refresh_token},
            headers={"accept": "application/json"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"request to Plaud's refresh endpoint failed: {exc}") from exc
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"refresh endpoint returned non-JSON: {exc}") from exc
    if resp.status_code >= 400 or not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError(f"Plaud token refresh failed: HTTP {resp.status_code} {data}")
    expires_in = data.get("expires_in")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_at": time.time() + float(expires_in) if expires_in is not None else None,
    }


def get_valid_access_token(ctx) -> tuple[str | None, str | None]:
    """``(token, error)`` — same contract as ``oauth.get_valid_access_token``.

    Unlike ``oauth.py``, this endpoint's error shape has never actually
    been observed (no completed real refresh yet) — a refresh failure is
    treated as transient (tokens kept for a retry) rather than guessing at
    a "revoked" signature that might not exist here."""
    data = _read_tokens(ctx)
    if data is None:
        return None, None
    expires_at = data.get("expires_at")
    if expires_at is None or expires_at - time.time() > REFRESH_MARGIN_S:
        return data["access_token"], None
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        disconnect(ctx)
        return None, None
    try:
        fresh = _refresh(refresh_token)
    except RuntimeError as exc:
        log.warning("plaud cli login: refresh failed (%s) — keeping stored tokens for a retry", exc)
        return None, str(exc)
    ctx.secrets.write(CLI_TOKENS_SECRET_KEY, json.dumps(fresh))
    return fresh["access_token"], None

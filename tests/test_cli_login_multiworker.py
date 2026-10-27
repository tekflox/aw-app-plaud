"""Cross-worker proof for ``CliLogin`` (plaud_app/cli_login.py) — two
independent ``CliLogin`` instances sharing one real Redis + one real flock
directory, mirroring aw-app-devctl's ``tests/test_relay_multiworker.py`` /
aw-workspace's own ``tests/integration/apps/test_state_facade_multiworker.py``.

Root cause (Kanban bug:plaud-cli-login-cross-worker-state): under
``AW_WORKSPACE_WORKERS=5``, ``POST /cli/login/start`` lands on one worker and
the follow-up ``GET /cli/login/status`` / ``POST /cli/login/complete`` land on
a random one of the five — only the originating worker's in-memory
``self._attempt`` ever had the real snapshot. This proves the fix (ownership
via ``ctx.state.lease``, the snapshot via ``ctx.state.kv``) the same way the
regression actually happens: ``start()`` on "worker A", ``status()``/
``complete()`` on "worker B" — a different ``CliLogin`` instance, a different
``AppContext``, nothing shared between them except the real Redis + the real
lock directory ``ctx.state`` resolves to.

Uses a fake ``subprocess.Popen`` (a queue-backed stdout so the test can drip
lines in on its own schedule) — never spawns the real ``npx`` CLI, same
posture as ``tests/test_cli_login.py``.

Needs a reachable Redis (``AW_TEST_REDIS_URL`` / ``AW_WORKSPACE_REDIS_URL`` /
``AW_REDIS_URL``, same resolution order ``state_facade.py`` itself uses) AND
aw-workspace core's own ``src`` package on ``sys.path`` (``ctx.state`` is only
importable inside an aw-workspace checkout) — skips cleanly without either,
same posture as its devctl/aw-backend precedents.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_WORKSPACE_ROOT = Path(os.environ.get("AW_WORKSPACE_CONTAINER_DIR", "/opt/aw-workspace"))
if (_WORKSPACE_ROOT / "src" / "apps" / "base.py").is_file():
    sys.path.insert(0, str(_WORKSPACE_ROOT))

import plaud_app.cli_login as cli_login  # noqa: E402


def _redis_url() -> str:
    for var in ("AW_TEST_REDIS_URL", "AW_WORKSPACE_REDIS_URL", "AW_REDIS_URL"):
        url = os.environ.get(var)
        if url:
            return url
    return "redis://127.0.0.1:6379/0"


def _src_available() -> bool:
    try:
        import src.apps.base  # noqa: F401
        return True
    except Exception:
        return False


def _redis_available() -> bool:
    if not _src_available():
        return False
    try:
        import redis as sync_redis
        client = sync_redis.Redis.from_url(_redis_url(), socket_connect_timeout=2)
        return bool(client.ping())
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _src_available(), reason="aw-workspace core's src package is not on sys.path"),
    pytest.mark.skipif(not _redis_available(), reason="Redis not reachable"),
]

# Isolate this run's keys from any real workspace sharing the same Redis.
os.environ["AW_WORKSPACE"] = f"plaud-cli-login-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _fresh_state_facade_client():
    """``ctx.state``'s kv client + breaker are cached at MODULE scope (one
    Redis connection per worker PROCESS, by design). Each test here runs its
    own ``asyncio.run(scenario())``, which opens and closes its own event
    loop — a client cached from a previous test's now-closed loop raises on
    first use and trips the breaker for 30s, silently degrading every
    kv call in whichever test runs next. Same reset
    ``test_state_facade_multiworker.py``/``test_relay_multiworker.py`` apply."""
    import src.apps.state_facade as sf
    sf._client = None
    sf._breaker = sf._StateBreaker()
    yield


class _FakeSecretStore:
    """Stand-in for the real file-based secret store — already confirmed
    shared correctly across workers (same container, same filesystem); a
    plain shared dict proves the point without touching disk."""

    def __init__(self):
        self._data: dict[tuple, str] = {}

    def get(self, app_id, key):
        return self._data.get((app_id, key))

    def put(self, app_id, key, value):
        self._data[(app_id, key)] = value

    def delete(self, app_id, key):
        return self._data.pop((app_id, key), None) is not None

    def keys(self, app_id):
        return [k for (a, k) in self._data if a == app_id]


def _fresh_ctx(secret_store, app_id: str = "plaud"):
    """A real, minimal ``AppContext`` granted ``state:own`` + ``secrets:own``
    — one per simulated worker, sharing nothing in Python with the other;
    only the Redis/flock ``ctx.state`` resolves to, and the one
    ``secret_store`` passed in (standing in for the real shared filesystem)."""
    from src.apps.base import AppContext

    class _Journal:
        def record(self, *a, **kw):
            pass

    _store = secret_store  # a class attribute can't be assigned from a same-named enclosing arg directly

    class _Runtime:
        journal = _Journal()
        secret_store = _store

    return AppContext(runtime=_Runtime(), app_id=app_id, version="1.0.0",
                      granted_permissions=["state:own", "secrets:own"], config={},
                      package_dir="/tmp/" + app_id)


class _QueueProc:
    """Fake ``Popen`` whose stdout is fed on the test's own schedule — a
    static ``iter(lines)`` (as ``tests/test_cli_login.py``'s ``_FakeProc``
    uses) can't simulate "worker A's reader thread observes a line well
    after start() already returned", which is exactly the timing this test
    needs to control."""

    def __init__(self, pid: int):
        self.pid = pid
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._returncode: int | None = None
        self.stdout = self._LineIter(self._lines)

    class _LineIter:
        def __init__(self, q):
            self._q = q

        def __iter__(self):
            return self

        def __next__(self):
            line = self._q.get()
            if line is None:
                raise StopIteration
            return line

    def send_line(self, line: str) -> None:
        self._lines.put(line)

    def finish(self, returncode: int = 0) -> None:
        self._returncode = returncode
        self._lines.put(None)

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode


async def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    async def _check():
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        return result

    deadline = time.time() + timeout
    while time.time() < deadline:
        if await _check():
            return True
        await asyncio.sleep(interval)
    return await _check()


def _neutralize_kill(monkeypatch):
    """These tests prove the ``ctx.state`` plumbing, not ``_kill``/
    ``_kill_by_pid`` (already unit-tested in ``test_cli_login.py``) — a fake
    pid reaching a real ``os.getpgid``/``killpg`` would be, at best, a
    no-op (``ProcessLookupError``) and at worst a signal sent to an
    unrelated real process if the fake pid ever collided with one."""
    monkeypatch.setattr(cli_login, "_kill", lambda proc: None)
    monkeypatch.setattr(cli_login, "_kill_by_pid", lambda pid: None)


class TestStatusCrossWorker:
    def test_status_on_worker_b_sees_worker_as_in_progress_attempt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
        _neutralize_kill(monkeypatch)
        store = _FakeSecretStore()

        def fake_popen(argv, **kwargs):
            return _QueueProc(pid=900001)
        monkeypatch.setattr(cli_login.subprocess, "Popen", fake_popen)

        async def scenario():
            ctx_a = _fresh_ctx(store)
            ctx_b = _fresh_ctx(store)
            mgr_a = cli_login.CliLogin(ctx_a)
            mgr_b = cli_login.CliLogin(ctx_b)

            await asyncio.to_thread(mgr_a.start)
            mgr_a._attempt.proc.send_line(
                "  https://web.plaud.ai/platform/oauth?client_id=abc&redirect_uri="
                "http%3A%2F%2Flocalhost%3A8199%2Fauth%2Fcallback&state=xyz")

            async def seen_on_b():
                status = await mgr_b.status()
                return status["phase"] == "waiting_for_browser"

            assert await _wait_until(seen_on_b), (
                "worker B's status() never saw worker A's in-progress attempt — "
                "this is the exact cross-worker gap AW_WORKSPACE_WORKERS opened up")
            status_b = await mgr_b.status()
            assert status_b["authorize_url"] == (
                "https://web.plaud.ai/platform/oauth?client_id=abc&redirect_uri="
                "http%3A%2F%2Flocalhost%3A8199%2Fauth%2Fcallback&state=xyz")

            # worker A never lost track of its own attempt either.
            status_a = await mgr_a.status()
            assert status_a["phase"] == "waiting_for_browser"

            mgr_a._attempt.proc.finish(0)

        asyncio.run(scenario())


class TestCompleteCrossWorker:
    def test_complete_on_worker_b_finishes_the_flow_worker_a_started(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
        _neutralize_kill(monkeypatch)
        store = _FakeSecretStore()

        def fake_popen(argv, **kwargs):
            return _QueueProc(pid=900002)
        monkeypatch.setattr(cli_login.subprocess, "Popen", fake_popen)

        tokens_written = {}

        def fake_get(url, params=None, timeout=None):
            # Simulate the CLI subprocess completing the exchange: it writes
            # the token file and prints its outcome line — observed only by
            # WORKER A's reader thread, never worker B's.
            now_ms = time.time() * 1000
            tokens_path = cli_login._tokens_path()
            tokens_path.parent.mkdir(parents=True, exist_ok=True)
            tokens_path.write_text(json.dumps({
                "access_token": "AT-XWORKER", "refresh_token": "RT-XWORKER",
                "expires_at": now_ms + 3600 * 1000,
            }))
            tokens_written["done"] = True
            proc_holder["proc"].send_line("  ✓ authenticated as fred@example.com.")

            class _Resp:
                status_code = 200
            return _Resp()

        monkeypatch.setattr(cli_login.httpx, "get", fake_get)
        proc_holder: dict = {}

        async def scenario():
            ctx_a = _fresh_ctx(store)
            ctx_b = _fresh_ctx(store)
            mgr_a = cli_login.CliLogin(ctx_a)
            mgr_b = cli_login.CliLogin(ctx_b)

            await asyncio.to_thread(mgr_a.start)
            proc_holder["proc"] = mgr_a._attempt.proc
            proc_holder["proc"].send_line(
                "  https://web.plaud.ai/platform/oauth?client_id=abc&redirect_uri="
                "http%3A%2F%2Flocalhost%3A8199%2Fauth%2Fcallback&state=xyz")

            async def seen_on_b():
                status = await mgr_b.status()
                return status["phase"] == "waiting_for_browser"
            assert await _wait_until(seen_on_b)

            # Worker B never ran start() — self._attempt is None there, same
            # as a real request landing on a different one of the 5 workers.
            assert mgr_b._attempt is None
            result = await asyncio.to_thread(
                mgr_b.complete, "http://127.0.0.1:8199/auth/callback?code=c1&state=s1")

            assert result == {"ok": True}, result
            assert tokens_written.get("done") is True
            stored = json.loads(store.get("plaud", cli_login.CLI_TOKENS_SECRET_KEY))
            assert stored["access_token"] == "AT-XWORKER"

            proc_holder["proc"].finish(0)

        asyncio.run(scenario())


class TestConcurrentStartCrossWorker:
    def test_concurrent_start_on_a_different_worker_does_not_spawn_a_second_subprocess(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("AW_WORKSPACE_HOME", str(tmp_path))
        _neutralize_kill(monkeypatch)
        store = _FakeSecretStore()

        popen_calls = []

        def fake_popen(argv, **kwargs):
            popen_calls.append(argv)
            return _QueueProc(pid=900003 + len(popen_calls))
        monkeypatch.setattr(cli_login.subprocess, "Popen", fake_popen)

        async def scenario():
            ctx_a = _fresh_ctx(store)
            ctx_b = _fresh_ctx(store)
            mgr_a = cli_login.CliLogin(ctx_a)
            mgr_b = cli_login.CliLogin(ctx_b)

            await asyncio.to_thread(mgr_a.start)
            proc_a = mgr_a._attempt.proc
            proc_a.send_line(
                "  https://web.plaud.ai/platform/oauth?client_id=abc&redirect_uri="
                "http%3A%2F%2Flocalhost%3A8199%2Fauth%2Fcallback&state=xyz")

            async def seen_on_b():
                status = await mgr_b.status()
                return status["phase"] == "waiting_for_browser"
            assert await _wait_until(seen_on_b)

            # A human clicks "Connect" again while A's attempt is still live,
            # and the request lands on worker B.
            result_b = await asyncio.to_thread(mgr_b.start)

            assert len(popen_calls) == 1, (
                "a second worker's start() must not spawn its own npx process "
                "while another worker's attempt is still live — that is the "
                "port-8199 collision this fix exists to prevent")
            assert result_b["phase"] == "waiting_for_browser"
            assert result_b["authorize_url"] is not None

            proc_a.finish(0)

        asyncio.run(scenario())

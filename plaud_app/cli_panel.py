"""The Settings iframe for the official-CLI login flow.

Why an iframe instead of more declarative-window widgets: the flow needs a
live countdown, a per-attempt clickable authorize link, and a paste-then-
submit step gated on that attempt's own state — none of which the
declarative widget vocabulary (aw-workspace-ui's `AppWindow.jsx`) can
express (its `button`+`poll` device-code block only renders a clickable
link via `user_code`/`verification_uri`, and its `pending_text` is a static,
author-time string — not per-attempt data). A small hand-written HTML/JS
page under our own control, embedded via the `iframe` widget, is the
established escape hatch for exactly this (see aw-app-call-agent's
`panel.py`, same pattern).
"""
from __future__ import annotations

PANEL_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Plaud — connect</title>
<style>
  :root { --bg:#0e1116; --panel:#161b22; --line:#262d38; --text:#e6edf3; --muted:#8b949e;
          --good:#3fb950; --bad:#f85149; --accent:#58a6ff; }
  * { box-sizing: border-box; }
  body { margin:0; padding:12px; background:var(--bg); color:var(--text);
         font:13px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  p { margin: 0 0 8px; }
  a { color: var(--accent); word-break: break-all; }
  button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line);
           background: var(--panel); color: var(--text); cursor: pointer; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #04101f; font-weight: 600; }
  input[type=text] { width: 100%; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--line);
           background: var(--bg); color: var(--text); }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px; margin-bottom: 10px; }
  .muted { color: var(--muted); }
  .good { color: var(--good); }
  .bad { color: var(--bad); }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .link-box { background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px; margin: 8px 0; }
  #root { display: flex; flex-direction: column; gap: 0; }
</style>
</head>
<body>
<div id="root"><p class="muted">Loading…</p></div>
<script>
(function () {
  var BASE = location.pathname.replace(/\/cli\/login\/panel\/?$/, '');
  var root = document.getElementById('root');
  var pollTimer = null;
  var lastCompleteError = null;

  function api(path, opts) {
    return fetch(BASE + path, Object.assign({ credentials: 'same-origin' }, opts))
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (body) {
        return { ok: r.ok, status: r.status, body: body };
      }); });
  }

  function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  function renderConnected(email) {
    stopPolling();
    root.innerHTML =
      '<div class="card"><p class="good">&#10003; Connected' + (email ? ' as ' + escapeHtml(email) : '') + '.</p>' +
      '<p class="muted">Refresh-token renewal is automatic from here — you will not need to repeat this.</p></div>';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function renderIdle(errorMsg) {
    stopPolling();
    root.innerHTML =
      '<div class="card">' +
      '<p>Connect using the official Plaud CLI (<code>@plaud-ai/mcp</code>), run inside this workspace. ' +
      'It opens a short-lived authorization link; you approve in your own browser, then paste back a URL ' +
      'that will look like a broken page — that\'s expected, read on.</p>' +
      (errorMsg ? '<p class="bad">' + escapeHtml(errorMsg) + '</p>' : '') +
      '<button class="primary" id="btn-start">Connect with Plaud</button>' +
      '</div>';
    document.getElementById('btn-start').onclick = start;
  }

  function renderWaiting(snap) {
    var mins = Math.floor(snap.seconds_remaining / 60);
    var secs = snap.seconds_remaining % 60;
    var timeText = mins + ':' + (secs < 10 ? '0' : '') + secs;
    root.innerHTML =
      '<div class="card">' +
      '<p><strong>1.</strong> Open this link in your own browser (wherever you\'re already logged into ' +
      'web.plaud.ai) and approve:</p>' +
      '<div class="link-box"><a href="' + escapeHtml(snap.authorize_url) + '" target="_blank" rel="noopener noreferrer">' +
      escapeHtml(snap.authorize_url) + '</a></div>' +
      '<p><strong>2.</strong> Plaud will redirect you to <code>localhost:8199</code> — on your machine, not ' +
      'this workspace\'s, so the tab will fail to connect. <em>That is expected</em>, not an error. Copy the ' +
      'full address from the browser\'s address bar at that point.</p>' +
      '<p><strong>3.</strong> Paste it here and complete the connection:</p>' +
      '<input type="text" id="callback-url" placeholder="http://localhost:8199/auth/callback?code=...&amp;state=..." />' +
      '<div class="row" style="margin-top:8px;">' +
      '<button class="primary" id="btn-complete">Complete connection</button>' +
      '<span class="muted">Time remaining: ' + timeText + '</span>' +
      '</div>' +
      (lastCompleteError ? '<p class="bad" style="margin-top:8px;">' + escapeHtml(lastCompleteError) + '</p>' : '') +
      '<p class="muted" style="margin-top:8px;">Wrong link, or ran out of time? ' +
      '<button id="btn-restart">Restart</button></p>' +
      '</div>';
    document.getElementById('btn-complete').onclick = complete;
    document.getElementById('btn-restart').onclick = start;
  }

  function renderTerminal(kind, message) {
    stopPolling();
    root.innerHTML =
      '<div class="card">' +
      '<p class="bad">' + (kind === 'timed_out' ? 'Timed out — the 2-minute window closed before the connection completed.' : escapeHtml(message || 'Something went wrong.')) + '</p>' +
      '<button class="primary" id="btn-restart">Restart</button>' +
      '</div>';
    document.getElementById('btn-restart').onclick = start;
  }

  function poll() {
    api('/cli/login/status').then(function (res) {
      var snap = res.body;
      if (snap.phase === 'idle') {
        renderIdle(null);
      } else if (snap.phase === 'waiting_for_browser') {
        renderWaiting(snap);
        pollTimer = setTimeout(poll, 3000);
      } else if (snap.phase === 'connected') {
        renderConnected(null);
      } else if (snap.phase === 'timed_out' || snap.phase === 'error') {
        renderTerminal(snap.phase, snap.error);
      } else {
        root.innerHTML = '<p class="muted">Starting the Plaud CLI…</p>';
        pollTimer = setTimeout(poll, 1500);
      }
    }).catch(function () {
      pollTimer = setTimeout(poll, 3000);
    });
  }

  function start() {
    stopPolling();
    lastCompleteError = null;
    root.innerHTML = '<p class="muted">Starting the Plaud CLI (first run downloads the package — a few seconds)…</p>';
    api('/cli/login/start', { method: 'POST' }).then(function () {
      poll();
    }).catch(function (e) {
      renderIdle('Could not start: ' + e.message);
    });
  }

  function complete() {
    var input = document.getElementById('callback-url');
    var url = (input && input.value || '').trim();
    if (!url) return;
    stopPolling();
    var btn = document.getElementById('btn-complete');
    if (btn) { btn.disabled = true; btn.textContent = 'Completing…'; }
    api('/cli/login/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_url: url }),
    }).then(function (res) {
      if (res.body && res.body.ok) {
        renderConnected(null);
        return;
      }
      lastCompleteError = (res.body && res.body.error) || 'Could not complete the connection.';
      poll();
    }).catch(function () {
      lastCompleteError = 'Network error while completing — check the workspace connection and retry.';
      poll();
    });
  }

  // Boot: don't assume idle — a browser refresh of this iframe should not
  // orphan an attempt already in flight (or hide an already-connected
  // state) just because our own in-memory poll loop restarted.
  api('/status').then(function (res) {
    if (res.body && res.body.auth_method === 'cli' && res.body.logged_in) {
      renderConnected(res.body.email);
      return;
    }
    poll();
  }).catch(function () {
    renderIdle(null);
  });
})();
</script>
</body>
</html>
"""

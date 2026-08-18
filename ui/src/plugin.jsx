// Integrated-mode entrypoint — dynamic-imported by aw-workspace-ui's
// loadComponentPlugin() once this app is installed with "ui:code" granted.
// Built by `npm run build` -> ui/dist/plaud-ui.mjs, referenced from
// aw-app.json's contributes.frontend.bundle. Same register(host)/JSX-factory
// pattern as aw-app-whiteboard/aw-app-presentations — see those for the full
// explanation of host.h/host.React closures and the "one shared React
// instance" ADR.
//
// One static window (unlike aw-app-presentations's per-id dynamic windows —
// there is exactly one Plaud account, so one window): PlaudWindowBody ->
// core.window.body:plaud.main. A connection strip at the top surfaces token
// state (connected / expiring soon / expired / not configured) as a
// first-class element instead of letting recordings silently 401 — that is
// the whole point of this app per the Kanban card that requested it.

const REFRESH_MS = 60_000;

export function register(host) {
  const { useState, useRef, useCallback, useEffect, useMemo } = host.React;

  function apiUrl(path) {
    return host.app.apiUrl(path);
  }

  async function apiFetch(path, opts) {
    const res = await host.sdk.api.fetch(apiUrl(path), opts);
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    return { ok: res.ok, status: res.status, data };
  }

  function formatDuration(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${m}:${String(rem).padStart(2, '0')}`;
  }

  function formatDate(ms) {
    if (!ms) return '';
    return new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function formatExpiry(expiresAt) {
    if (!expiresAt) return null;
    const deltaS = expiresAt - Date.now() / 1000;
    if (deltaS <= 0) return { text: 'expired', danger: true };
    const hours = deltaS / 3600;
    if (hours < 1) return { text: `expires in ${Math.round(deltaS / 60)}m`, danger: true };
    if (hours < 6) return { text: `expires in ${hours.toFixed(1)}h`, danger: true };
    return { text: `expires in ${Math.round(hours)}h`, danger: false };
  }

  // ------------------------------------------------------------------
  // Connection panel — token state + paste-a-token form. Rendered inline
  // at the top of the window (compact) and, when there's no usable
  // connection yet, expanded to fill the window so the empty state IS
  // the settings form rather than a blank list.
  // ------------------------------------------------------------------
  function ConnectionPanel({ status, onSaved, expanded, onToggle }) {
    const [tokenInput, setTokenInput] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    const save = useCallback(async () => {
      const token = tokenInput.trim();
      if (!token) return;
      setSaving(true);
      setError(null);
      const { ok, data } = await apiFetch('/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plaud_bearer_token: token }),
      });
      setSaving(false);
      if (!ok || !data || data.error) {
        setError((data && (data.error || data.detail)) || 'Failed to save token.');
        return;
      }
      setTokenInput('');
      onSaved(data);
    }, [tokenInput, onSaved]);

    const expiry = formatExpiry(status?.expires_at);
    const badge = !status?.configured
      ? { text: 'Not connected', className: 'bg-white/10 text-[var(--color-text-muted)]' }
      : status.expired
      ? { text: 'Token expired', className: 'bg-[var(--color-danger)]/20 text-[var(--color-danger)]' }
      : status.logged_in
      ? { text: status.email ? `Connected — ${status.email}` : 'Connected', className: 'bg-emerald-500/20 text-emerald-400' }
      : { text: status.error || 'Connection error', className: 'bg-[var(--color-danger)]/20 text-[var(--color-danger)]' };

    return (
      <div className="border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]">
        <div className="flex items-center justify-between px-3 py-2 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${badge.className}`}>
              {badge.text}
            </span>
            {expiry && (
              <span className={`text-[10px] ${expiry.danger ? 'text-[var(--color-danger)]' : 'text-[var(--color-text-muted)]'}`}>
                {expiry.text}
              </span>
            )}
          </div>
          <button
            onClick={onToggle}
            className="text-[10px] px-2 py-1 rounded hover:bg-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] whitespace-nowrap"
          >
            {expanded ? 'Hide' : status?.configured ? 'Update token' : 'Connect'}
          </button>
        </div>

        {expanded && (
          <div className="px-3 pb-3">
            <div className="text-[11px] text-[var(--color-text-muted)] mb-2">
              Paste a fresh bearer token from a logged-in{' '}
              <code className="bg-white/10 px-1 rounded">web.plaud.ai</code> browser session
              (DevTools → Network → any <code className="bg-white/10 px-1 rounded">api.plaud.ai</code> request
              → <code className="bg-white/10 px-1 rounded">authorization</code> header). It's a short-lived
              token (~24h) with no auto-refresh — see the <code className="bg-white/10 px-1 rounded">aw-plaud</code> skill
              for the full steps.
            </div>
            <div className="flex gap-2">
              <input
                type="password"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') save(); }}
                placeholder="Bearer eyJ..."
                className="flex-1 text-[11px] bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)]"
              />
              <button
                onClick={save}
                disabled={saving || !tokenInput.trim()}
                className="text-[11px] px-3 py-1.5 rounded bg-[var(--color-accent)]/20 text-[var(--color-accent)] hover:bg-[var(--color-accent)]/30 transition-colors disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
            {error && <div className="text-[10px] text-[var(--color-danger)] mt-1.5">{error}</div>}
          </div>
        )}
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Recording list — left pane
  // ------------------------------------------------------------------
  function RecordingRow({ recording, selected, onClick }) {
    return (
      <div
        onClick={onClick}
        className={`px-3 py-2 border-b border-[var(--color-border)] cursor-pointer transition-colors ${
          selected ? 'bg-[var(--color-accent)]/15' : 'hover:bg-white/5'
        }`}
      >
        <div className="text-[12px] font-medium text-[var(--color-text-primary)] truncate">{recording.filename}</div>
        <div className="flex items-center gap-2 mt-1 text-[10px] text-[var(--color-text-muted)]">
          <span>{formatDate(recording.start_time_ms)}</span>
          <span>·</span>
          <span>{formatDuration(recording.duration_s)}</span>
          {recording.is_transcribed && (
            <span className="px-1.5 py-0.5 rounded-full bg-white/10">transcript</span>
          )}
          {recording.has_summary && (
            <span className="px-1.5 py-0.5 rounded-full bg-white/10">summary</span>
          )}
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Detail pane — player, download, transcript, summary
  // ------------------------------------------------------------------
  function RecordingDetail({ recording }) {
    const [tab, setTab] = useState('transcript');
    const [transcript, setTranscript] = useState(null);
    const [summary, setSummary] = useState(null);
    const [loadError, setLoadError] = useState(null);
    const [loading, setLoading] = useState(false);

    const audioUrl = recording ? host.app.absoluteApiUrl(`/recordings/${encodeURIComponent(recording.id)}/audio`) : null;
    const downloadUrl = recording ? host.app.absoluteApiUrl(`/recordings/${encodeURIComponent(recording.id)}/audio?download=true`) : null;

    useEffect(() => {
      setTranscript(null);
      setSummary(null);
      setLoadError(null);
      setTab(recording?.is_transcribed ? 'transcript' : recording?.has_summary ? 'summary' : 'transcript');
    }, [recording?.id]);

    useEffect(() => {
      if (!recording) return;
      let cancelled = false;
      const load = async () => {
        setLoading(true);
        setLoadError(null);
        try {
          if (tab === 'transcript' && transcript === null) {
            const { ok, data } = await apiFetch(`/recordings/${encodeURIComponent(recording.id)}/transcript`);
            if (cancelled) return;
            if (!ok) setLoadError((data && data.error) || 'Failed to load transcript.');
            else setTranscript(data.segments || []);
          } else if (tab === 'summary' && summary === null) {
            const { ok, data } = await apiFetch(`/recordings/${encodeURIComponent(recording.id)}/summary`);
            if (cancelled) return;
            if (!ok) setLoadError((data && data.error) || 'Failed to load summary.');
            else setSummary(data.summary || '');
          }
        } finally {
          if (!cancelled) setLoading(false);
        }
      };
      load();
      return () => { cancelled = true; };
    }, [recording?.id, tab]);

    if (!recording) {
      return (
        <div className="flex-1 flex items-center justify-center text-[12px] text-[var(--color-text-muted)]">
          Select a recording
        </div>
      );
    }

    return (
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-3 py-2 border-b border-[var(--color-border)]">
          <div className="text-[13px] font-medium text-[var(--color-text-primary)] truncate mb-2">{recording.filename}</div>
          <div className="flex items-center gap-2">
            <audio controls preload="none" src={audioUrl} className="flex-1 h-8" style={{ maxWidth: 420 }} />
            <a
              href={downloadUrl}
              className="text-[11px] px-2 py-1 rounded bg-white/10 hover:bg-white/20 text-[var(--color-text-primary)] whitespace-nowrap"
            >
              Download
            </a>
          </div>
        </div>

        <div className="flex border-b border-[var(--color-border)]">
          {['transcript', 'summary'].map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 text-[11px] capitalize border-b-2 transition-colors ${
                tab === t
                  ? 'border-[var(--color-accent)] text-[var(--color-text-primary)]'
                  : 'border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-2">
          {loading && <div className="text-[11px] text-[var(--color-text-muted)]">Loading…</div>}
          {!loading && loadError && <div className="text-[11px] text-[var(--color-danger)]">{loadError}</div>}
          {!loading && !loadError && tab === 'transcript' && (
            transcript && transcript.length > 0 ? (
              <div className="flex flex-col gap-1.5">
                {transcript.map((seg, i) => (
                  <div key={i} className="flex gap-2 text-[11px]">
                    <span className="text-[var(--color-text-muted)] font-mono whitespace-nowrap">{formatDuration(seg.start_s)}</span>
                    <span className="text-[var(--color-text-primary)]">{seg.text}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-[var(--color-text-muted)] italic">No transcript yet.</div>
            )
          )}
          {!loading && !loadError && tab === 'summary' && (
            summary ? (
              <div className="text-[12px] text-[var(--color-text-primary)] whitespace-pre-wrap">{summary}</div>
            ) : (
              <div className="text-[11px] text-[var(--color-text-muted)] italic">No summary yet.</div>
            )
          )}
        </div>
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Window body
  // ------------------------------------------------------------------
  function PlaudWindowBody() {
    const [status, setStatus] = useState(null);
    const [connectionExpanded, setConnectionExpanded] = useState(false);
    const [recordings, setRecordings] = useState([]);
    const [selectedId, setSelectedId] = useState(null);
    const [listError, setListError] = useState(null);
    const [listLoading, setListLoading] = useState(false);

    const loadStatus = useCallback(async () => {
      const { data } = await apiFetch('/status');
      if (data) {
        setStatus(data);
        if (!data.configured || data.expired) setConnectionExpanded(true);
      }
    }, []);

    const loadRecordings = useCallback(async () => {
      setListLoading(true);
      setListError(null);
      const { ok, data } = await apiFetch('/recordings?limit=50');
      setListLoading(false);
      if (!ok) {
        setListError((data && data.error) || 'Failed to load recordings.');
        return;
      }
      setRecordings(Array.isArray(data) ? data : []);
    }, []);

    useEffect(() => { loadStatus(); }, [loadStatus]);

    useEffect(() => {
      const interval = setInterval(loadStatus, REFRESH_MS);
      return () => clearInterval(interval);
    }, [loadStatus]);

    useEffect(() => {
      if (status?.logged_in) loadRecordings();
    }, [status?.logged_in, loadRecordings]);

    const selected = useMemo(
      () => recordings.find((r) => r.id === selectedId) || null,
      [recordings, selectedId],
    );

    return (
      <div className="flex flex-col h-full bg-[var(--color-bg-primary)]">
        <ConnectionPanel
          status={status}
          expanded={connectionExpanded}
          onToggle={() => setConnectionExpanded((v) => !v)}
          onSaved={() => { setConnectionExpanded(false); loadStatus(); }}
        />

        {status?.logged_in ? (
          <div className="flex-1 flex min-h-0">
            <div className="w-64 shrink-0 border-r border-[var(--color-border)] overflow-y-auto">
              {listLoading && recordings.length === 0 && (
                <div className="px-3 py-4 text-[11px] text-[var(--color-text-muted)] text-center">Loading recordings…</div>
              )}
              {listError && (
                <div className="px-3 py-4 text-[11px] text-[var(--color-danger)]">{listError}</div>
              )}
              {!listLoading && !listError && recordings.length === 0 && (
                <div className="px-3 py-4 text-[11px] text-[var(--color-text-muted)] text-center italic">No recordings found.</div>
              )}
              {recordings.map((r) => (
                <RecordingRow key={r.id} recording={r} selected={r.id === selectedId} onClick={() => setSelectedId(r.id)} />
              ))}
            </div>
            <RecordingDetail recording={selected} />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-center px-6">
            <div className="text-[12px] text-[var(--color-text-muted)] max-w-sm">
              {status?.configured
                ? 'Connect a valid token above to see your recordings.'
                : 'Connect your Plaud account above to see your recordings, play audio, and read transcripts.'}
            </div>
          </div>
        )}
      </div>
    );
  }

  host.registerWindow('plaud.main', PlaudWindowBody);
}

export default register;

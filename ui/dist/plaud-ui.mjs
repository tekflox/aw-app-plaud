function L(t) {
  const { useState: i, useRef: U, useCallback: h, useEffect: g, useMemo: k } = t.React;
  function C(e) {
    return t.app.apiUrl(e);
  }
  async function N(e, r) {
    const o = await t.sdk.api.fetch(C(e), r);
    let l = null;
    try {
      l = await o.json();
    } catch {
    }
    return { ok: o.ok, status: o.status, data: l };
  }
  function w(e) {
    const r = Math.max(0, Math.floor(e || 0)), o = Math.floor(r / 60), l = r % 60;
    return `${o}:${String(l).padStart(2, "0")}`;
  }
  function S(e) {
    return e ? new Date(e).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  }
  function _(e) {
    if (!e) return null;
    const r = e - Date.now() / 1e3;
    if (r <= 0) return { text: "expired", danger: !0 };
    const o = r / 3600;
    return o < 1 ? { text: `expires in ${Math.round(r / 60)}m`, danger: !0 } : o < 6 ? { text: `expires in ${o.toFixed(1)}h`, danger: !0 } : { text: `expires in ${Math.round(o)}h`, danger: !1 };
  }
  function R({ status: e, onSaved: r, expanded: o, onToggle: l }) {
    const [c, f] = i(""), [p, m] = i(!1), [s, x] = i(null), v = h(async () => {
      const n = c.trim();
      if (!n) return;
      m(!0), x(null);
      const { ok: b, data: a } = await N("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plaud_bearer_token: n })
      });
      if (m(!1), !b || !a || a.error) {
        x(a && (a.error || a.detail) || "Failed to save token.");
        return;
      }
      f(""), r(a);
    }, [c, r]), y = _(e == null ? void 0 : e.expires_at), u = e != null && e.configured ? e.expired ? { text: "Token expired", className: "bg-[var(--color-danger)]/20 text-[var(--color-danger)]" } : e.logged_in ? { text: e.email ? `Connected — ${e.email}` : "Connected", className: "bg-emerald-500/20 text-emerald-400" } : { text: e.error || "Connection error", className: "bg-[var(--color-danger)]/20 text-[var(--color-danger)]" } : { text: "Not connected", className: "bg-white/10 text-[var(--color-text-muted)]" };
    return /* @__PURE__ */ t.h("div", { className: "border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]" }, /* @__PURE__ */ t.h("div", { className: "flex items-center justify-between px-3 py-2 gap-2" }, /* @__PURE__ */ t.h("div", { className: "flex items-center gap-2 min-w-0" }, /* @__PURE__ */ t.h("span", { className: `text-[10px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${u.className}` }, u.text), y && /* @__PURE__ */ t.h("span", { className: `text-[10px] ${y.danger ? "text-[var(--color-danger)]" : "text-[var(--color-text-muted)]"}` }, y.text)), /* @__PURE__ */ t.h(
      "button",
      {
        onClick: l,
        className: "text-[10px] px-2 py-1 rounded hover:bg-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] whitespace-nowrap"
      },
      o ? "Hide" : e != null && e.configured ? "Update token" : "Connect"
    )), o && /* @__PURE__ */ t.h("div", { className: "px-3 pb-3" }, /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)] mb-2" }, "Paste a fresh bearer token from a logged-in", " ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "web.plaud.ai"), " browser session (DevTools → Network → any ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "api.plaud.ai"), " request → ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "authorization"), " header). It's a short-lived token (~24h) with no auto-refresh — see the ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "aw-plaud"), " skill for the full steps."), /* @__PURE__ */ t.h("div", { className: "flex gap-2" }, /* @__PURE__ */ t.h(
      "input",
      {
        type: "password",
        value: c,
        onChange: (n) => f(n.target.value),
        onKeyDown: (n) => {
          n.key === "Enter" && v();
        },
        placeholder: "Bearer eyJ...",
        className: "flex-1 text-[11px] bg-[var(--color-bg-primary)] border border-[var(--color-border)] rounded px-2 py-1.5 text-[var(--color-text-primary)] outline-none focus:border-[var(--color-accent)]"
      }
    ), /* @__PURE__ */ t.h(
      "button",
      {
        onClick: v,
        disabled: p || !c.trim(),
        className: "text-[11px] px-3 py-1.5 rounded bg-[var(--color-accent)]/20 text-[var(--color-accent)] hover:bg-[var(--color-accent)]/30 transition-colors disabled:opacity-40"
      },
      p ? "Saving…" : "Save"
    )), s && /* @__PURE__ */ t.h("div", { className: "text-[10px] text-[var(--color-danger)] mt-1.5" }, s)));
  }
  function E({ recording: e, selected: r, onClick: o }) {
    return /* @__PURE__ */ t.h(
      "div",
      {
        onClick: o,
        className: `px-3 py-2 border-b border-[var(--color-border)] cursor-pointer transition-colors ${r ? "bg-[var(--color-accent)]/15" : "hover:bg-white/5"}`
      },
      /* @__PURE__ */ t.h("div", { className: "text-[12px] font-medium text-[var(--color-text-primary)] truncate" }, e.filename),
      /* @__PURE__ */ t.h("div", { className: "flex items-center gap-2 mt-1 text-[10px] text-[var(--color-text-muted)]" }, /* @__PURE__ */ t.h("span", null, S(e.start_time_ms)), /* @__PURE__ */ t.h("span", null, "·"), /* @__PURE__ */ t.h("span", null, w(e.duration_s)), e.is_transcribed && /* @__PURE__ */ t.h("span", { className: "px-1.5 py-0.5 rounded-full bg-white/10" }, "transcript"), e.has_summary && /* @__PURE__ */ t.h("span", { className: "px-1.5 py-0.5 rounded-full bg-white/10" }, "summary"))
    );
  }
  function $({ recording: e }) {
    const [r, o] = i("transcript"), [l, c] = i(null), [f, p] = i(null), [m, s] = i(null), [x, v] = i(!1), y = e ? t.app.absoluteApiUrl(`/recordings/${encodeURIComponent(e.id)}/audio`) : null, u = e ? t.app.absoluteApiUrl(`/recordings/${encodeURIComponent(e.id)}/audio?download=true`) : null;
    return g(() => {
      c(null), p(null), s(null), o(e != null && e.is_transcribed ? "transcript" : e != null && e.has_summary ? "summary" : "transcript");
    }, [e == null ? void 0 : e.id]), g(() => {
      if (!e) return;
      let n = !1;
      return (async () => {
        v(!0), s(null);
        try {
          if (r === "transcript" && l === null) {
            const { ok: a, data: d } = await N(`/recordings/${encodeURIComponent(e.id)}/transcript`);
            if (n) return;
            a ? c(d.segments || []) : s(d && d.error || "Failed to load transcript.");
          } else if (r === "summary" && f === null) {
            const { ok: a, data: d } = await N(`/recordings/${encodeURIComponent(e.id)}/summary`);
            if (n) return;
            a ? p(d.summary || "") : s(d && d.error || "Failed to load summary.");
          }
        } finally {
          n || v(!1);
        }
      })(), () => {
        n = !0;
      };
    }, [e == null ? void 0 : e.id, r]), e ? /* @__PURE__ */ t.h("div", { className: "flex-1 flex flex-col min-w-0" }, /* @__PURE__ */ t.h("div", { className: "px-3 py-2 border-b border-[var(--color-border)]" }, /* @__PURE__ */ t.h("div", { className: "text-[13px] font-medium text-[var(--color-text-primary)] truncate mb-2" }, e.filename), /* @__PURE__ */ t.h("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ t.h("audio", { controls: !0, preload: "none", src: y, className: "flex-1 h-8", style: { maxWidth: 420 } }), /* @__PURE__ */ t.h(
      "a",
      {
        href: u,
        className: "text-[11px] px-2 py-1 rounded bg-white/10 hover:bg-white/20 text-[var(--color-text-primary)] whitespace-nowrap"
      },
      "Download"
    ))), /* @__PURE__ */ t.h("div", { className: "flex border-b border-[var(--color-border)]" }, ["transcript", "summary"].map((n) => /* @__PURE__ */ t.h(
      "button",
      {
        key: n,
        onClick: () => o(n),
        className: `px-3 py-1.5 text-[11px] capitalize border-b-2 transition-colors ${r === n ? "border-[var(--color-accent)] text-[var(--color-text-primary)]" : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"}`
      },
      n
    ))), /* @__PURE__ */ t.h("div", { className: "flex-1 overflow-y-auto px-3 py-2" }, x && /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)]" }, "Loading…"), !x && m && /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-danger)]" }, m), !x && !m && r === "transcript" && (l && l.length > 0 ? /* @__PURE__ */ t.h("div", { className: "flex flex-col gap-1.5" }, l.map((n, b) => /* @__PURE__ */ t.h("div", { key: b, className: "flex gap-2 text-[11px]" }, /* @__PURE__ */ t.h("span", { className: "text-[var(--color-text-muted)] font-mono whitespace-nowrap" }, w(n.start_s)), /* @__PURE__ */ t.h("span", { className: "text-[var(--color-text-primary)]" }, n.text)))) : /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)] italic" }, "No transcript yet.")), !x && !m && r === "summary" && (f ? /* @__PURE__ */ t.h("div", { className: "text-[12px] text-[var(--color-text-primary)] whitespace-pre-wrap" }, f) : /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)] italic" }, "No summary yet.")))) : /* @__PURE__ */ t.h("div", { className: "flex-1 flex items-center justify-center text-[12px] text-[var(--color-text-muted)]" }, "Select a recording");
  }
  function I() {
    const [e, r] = i(null), [o, l] = i(!1), [c, f] = i([]), [p, m] = i(null), [s, x] = i(null), [v, y] = i(!1), u = h(async () => {
      const { data: a } = await N("/status");
      a && (r(a), (!a.configured || a.expired) && l(!0));
    }, []), n = h(async () => {
      y(!0), x(null);
      const { ok: a, data: d } = await N("/recordings?limit=50");
      if (y(!1), !a) {
        x(d && d.error || "Failed to load recordings.");
        return;
      }
      f(Array.isArray(d) ? d : []);
    }, []);
    g(() => {
      u();
    }, [u]), g(() => {
      const a = setInterval(u, 6e4);
      return () => clearInterval(a);
    }, [u]), g(() => {
      e != null && e.logged_in && n();
    }, [e == null ? void 0 : e.logged_in, n]);
    const b = k(
      () => c.find((a) => a.id === p) || null,
      [c, p]
    );
    return /* @__PURE__ */ t.h("div", { className: "flex flex-col h-full bg-[var(--color-bg-primary)]" }, /* @__PURE__ */ t.h(
      R,
      {
        status: e,
        expanded: o,
        onToggle: () => l((a) => !a),
        onSaved: (a) => {
          r(a), l(!1);
        }
      }
    ), e != null && e.logged_in ? /* @__PURE__ */ t.h("div", { className: "flex-1 flex min-h-0" }, /* @__PURE__ */ t.h("div", { className: "w-64 shrink-0 border-r border-[var(--color-border)] overflow-y-auto" }, v && c.length === 0 && /* @__PURE__ */ t.h("div", { className: "px-3 py-4 text-[11px] text-[var(--color-text-muted)] text-center" }, "Loading recordings…"), s && /* @__PURE__ */ t.h("div", { className: "px-3 py-4 text-[11px] text-[var(--color-danger)]" }, s), !v && !s && c.length === 0 && /* @__PURE__ */ t.h("div", { className: "px-3 py-4 text-[11px] text-[var(--color-text-muted)] text-center italic" }, "No recordings found."), c.map((a) => /* @__PURE__ */ t.h(E, { key: a.id, recording: a, selected: a.id === p, onClick: () => m(a.id) }))), /* @__PURE__ */ t.h($, { recording: b })) : /* @__PURE__ */ t.h("div", { className: "flex-1 flex items-center justify-center text-center px-6" }, /* @__PURE__ */ t.h("div", { className: "text-[12px] text-[var(--color-text-muted)] max-w-sm" }, e != null && e.configured ? "Connect a valid token above to see your recordings." : "Connect your Plaud account above to see your recordings, play audio, and read transcripts.")));
  }
  t.registerWindow("plaud.main", I);
}
export {
  L as default,
  L as register
};

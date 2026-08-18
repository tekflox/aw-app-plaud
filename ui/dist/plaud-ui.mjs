function M(t) {
  const { useState: c, useRef: U, useCallback: N, useEffect: g, useMemo: k } = t.React;
  function S(e) {
    return t.app.apiUrl(e);
  }
  async function h(e, r) {
    const o = await t.sdk.api.fetch(S(e), r);
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
  function C(e) {
    return e ? new Date(e).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  }
  function _(e) {
    if (!e) return null;
    const r = e - Date.now() / 1e3;
    if (r <= 0) {
      const l = Math.floor(-r / 86400);
      if (l >= 1) return { text: `expired ${l}d ago`, danger: !0 };
      const i = Math.floor(-r / 3600);
      return i >= 1 ? { text: `expired ${i}h ago`, danger: !0 } : { text: "expired", danger: !0 };
    }
    const o = r / 3600;
    return o < 1 ? { text: `expires in ${Math.round(r / 60)}m`, danger: !0 } : o < 6 ? { text: `expires in ${o.toFixed(1)}h`, danger: !0 } : { text: `expires in ${Math.round(o)}h`, danger: !1 };
  }
  function $({ status: e, onSaved: r, expanded: o, onToggle: l }) {
    const [i, f] = c(""), [m, u] = c(!1), [s, x] = c(null), v = N(async () => {
      const n = i.trim();
      if (!n) return;
      u(!0), x(null);
      const { ok: b, data: a } = await h("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plaud_bearer_token: n })
      });
      if (u(!1), !b || !a || a.error) {
        x(a && (a.error || a.detail) || "Failed to save token.");
        return;
      }
      f(""), r(a);
    }, [i, r]), y = _(e == null ? void 0 : e.expires_at), p = e != null && e.configured ? e.expired ? { text: "Token expired", className: "bg-[var(--color-danger)]/20 text-[var(--color-danger)]" } : e.logged_in ? { text: e.email ? `Connected — ${e.email}` : "Connected", className: "bg-emerald-500/20 text-emerald-400" } : { text: e.error || "Connection error", className: "bg-[var(--color-danger)]/20 text-[var(--color-danger)]" } : { text: "Not connected", className: "bg-white/10 text-[var(--color-text-muted)]" };
    return /* @__PURE__ */ t.h("div", { className: "border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)]" }, /* @__PURE__ */ t.h("div", { className: "flex items-center justify-between px-3 py-2 gap-2" }, /* @__PURE__ */ t.h("div", { className: "flex items-center gap-2 min-w-0" }, /* @__PURE__ */ t.h("span", { className: `text-[10px] px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${p.className}` }, p.text), y && /* @__PURE__ */ t.h("span", { className: `text-[10px] ${y.danger ? "text-[var(--color-danger)]" : "text-[var(--color-text-muted)]"}` }, y.text)), /* @__PURE__ */ t.h(
      "button",
      {
        onClick: l,
        className: "text-[10px] px-2 py-1 rounded hover:bg-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] whitespace-nowrap"
      },
      o ? "Hide" : e != null && e.configured ? "Update token" : "Connect"
    )), o && /* @__PURE__ */ t.h("div", { className: "px-3 pb-3" }, /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)] mb-2" }, "Open", " ", /* @__PURE__ */ t.h(
      "a",
      {
        href: "https://web.plaud.ai",
        target: "_blank",
        rel: "noopener noreferrer",
        className: "text-[var(--color-accent)] hover:underline"
      },
      "web.plaud.ai"
    ), " ", "and log in, then DevTools → Network → any", " ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "api.plaud.ai"), " request →", " ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "authorization"), " header. Paste it below as-is — with or without ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "Bearer "), ", or the whole header line, both work. It's a short-lived token (~24h) with no auto-refresh — see the", " ", /* @__PURE__ */ t.h("code", { className: "bg-white/10 px-1 rounded" }, "aw-plaud"), " skill for the full steps. This panel is also reachable from this app's Settings, which is now the canonical place to manage it."), /* @__PURE__ */ t.h("div", { className: "flex gap-2" }, /* @__PURE__ */ t.h(
      "input",
      {
        type: "password",
        value: i,
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
        disabled: m || !i.trim(),
        className: "text-[11px] px-3 py-1.5 rounded bg-[var(--color-accent)]/20 text-[var(--color-accent)] hover:bg-[var(--color-accent)]/30 transition-colors disabled:opacity-40"
      },
      m ? "Saving…" : "Save"
    )), s && /* @__PURE__ */ t.h("div", { className: "text-[10px] text-[var(--color-danger)] mt-1.5" }, s)));
  }
  function R({ recording: e, selected: r, onClick: o }) {
    return /* @__PURE__ */ t.h(
      "div",
      {
        onClick: o,
        className: `px-3 py-2 border-b border-[var(--color-border)] cursor-pointer transition-colors ${r ? "bg-[var(--color-accent)]/15" : "hover:bg-white/5"}`
      },
      /* @__PURE__ */ t.h("div", { className: "text-[12px] font-medium text-[var(--color-text-primary)] truncate" }, e.filename),
      /* @__PURE__ */ t.h("div", { className: "flex items-center gap-2 mt-1 text-[10px] text-[var(--color-text-muted)]" }, /* @__PURE__ */ t.h("span", null, C(e.start_time_ms)), /* @__PURE__ */ t.h("span", null, "·"), /* @__PURE__ */ t.h("span", null, w(e.duration_s)), e.is_transcribed && /* @__PURE__ */ t.h("span", { className: "px-1.5 py-0.5 rounded-full bg-white/10" }, "transcript"), e.has_summary && /* @__PURE__ */ t.h("span", { className: "px-1.5 py-0.5 rounded-full bg-white/10" }, "summary"))
    );
  }
  function E({ recording: e }) {
    const [r, o] = c("transcript"), [l, i] = c(null), [f, m] = c(null), [u, s] = c(null), [x, v] = c(!1), y = e ? t.app.absoluteApiUrl(`/recordings/${encodeURIComponent(e.id)}/audio`) : null, p = e ? t.app.absoluteApiUrl(`/recordings/${encodeURIComponent(e.id)}/audio?download=true`) : null;
    return g(() => {
      i(null), m(null), s(null), o(e != null && e.is_transcribed ? "transcript" : e != null && e.has_summary ? "summary" : "transcript");
    }, [e == null ? void 0 : e.id]), g(() => {
      if (!e) return;
      let n = !1;
      return (async () => {
        v(!0), s(null);
        try {
          if (r === "transcript" && l === null) {
            const { ok: a, data: d } = await h(`/recordings/${encodeURIComponent(e.id)}/transcript`);
            if (n) return;
            a ? i(d.segments || []) : s(d && d.error || "Failed to load transcript.");
          } else if (r === "summary" && f === null) {
            const { ok: a, data: d } = await h(`/recordings/${encodeURIComponent(e.id)}/summary`);
            if (n) return;
            a ? m(d.summary || "") : s(d && d.error || "Failed to load summary.");
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
        href: p,
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
    ))), /* @__PURE__ */ t.h("div", { className: "flex-1 overflow-y-auto px-3 py-2" }, x && /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)]" }, "Loading…"), !x && u && /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-danger)]" }, u), !x && !u && r === "transcript" && (l && l.length > 0 ? /* @__PURE__ */ t.h("div", { className: "flex flex-col gap-1.5" }, l.map((n, b) => /* @__PURE__ */ t.h("div", { key: b, className: "flex gap-2 text-[11px]" }, /* @__PURE__ */ t.h("span", { className: "text-[var(--color-text-muted)] font-mono whitespace-nowrap" }, w(n.start_s)), /* @__PURE__ */ t.h("span", { className: "text-[var(--color-text-primary)]" }, n.text)))) : /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)] italic" }, "No transcript yet.")), !x && !u && r === "summary" && (f ? /* @__PURE__ */ t.h("div", { className: "text-[12px] text-[var(--color-text-primary)] whitespace-pre-wrap" }, f) : /* @__PURE__ */ t.h("div", { className: "text-[11px] text-[var(--color-text-muted)] italic" }, "No summary yet.")))) : /* @__PURE__ */ t.h("div", { className: "flex-1 flex items-center justify-center text-[12px] text-[var(--color-text-muted)]" }, "Select a recording");
  }
  function I() {
    const [e, r] = c(null), [o, l] = c(!1), [i, f] = c([]), [m, u] = c(null), [s, x] = c(null), [v, y] = c(!1), p = N(async () => {
      const { data: a } = await h("/status");
      a && (r(a), (!a.configured || a.expired) && l(!0));
    }, []), n = N(async () => {
      y(!0), x(null);
      const { ok: a, data: d } = await h("/recordings?limit=50");
      if (y(!1), !a) {
        x(d && d.error || "Failed to load recordings.");
        return;
      }
      f(Array.isArray(d) ? d : []);
    }, []);
    g(() => {
      p();
    }, [p]), g(() => {
      const a = setInterval(p, 6e4);
      return () => clearInterval(a);
    }, [p]), g(() => {
      e != null && e.logged_in && n();
    }, [e == null ? void 0 : e.logged_in, n]);
    const b = k(
      () => i.find((a) => a.id === m) || null,
      [i, m]
    );
    return /* @__PURE__ */ t.h("div", { className: "flex flex-col h-full bg-[var(--color-bg-primary)]" }, /* @__PURE__ */ t.h(
      $,
      {
        status: e,
        expanded: o,
        onToggle: () => l((a) => !a),
        onSaved: () => {
          l(!1), p();
        }
      }
    ), e != null && e.logged_in ? /* @__PURE__ */ t.h("div", { className: "flex-1 flex min-h-0" }, /* @__PURE__ */ t.h("div", { className: "w-64 shrink-0 border-r border-[var(--color-border)] overflow-y-auto" }, v && i.length === 0 && /* @__PURE__ */ t.h("div", { className: "px-3 py-4 text-[11px] text-[var(--color-text-muted)] text-center" }, "Loading recordings…"), s && /* @__PURE__ */ t.h("div", { className: "px-3 py-4 text-[11px] text-[var(--color-danger)]" }, s), !v && !s && i.length === 0 && /* @__PURE__ */ t.h("div", { className: "px-3 py-4 text-[11px] text-[var(--color-text-muted)] text-center italic" }, "No recordings found."), i.map((a) => /* @__PURE__ */ t.h(R, { key: a.id, recording: a, selected: a.id === m, onClick: () => u(a.id) }))), /* @__PURE__ */ t.h(E, { recording: b })) : /* @__PURE__ */ t.h("div", { className: "flex-1 flex items-center justify-center text-center px-6" }, /* @__PURE__ */ t.h("div", { className: "text-[12px] text-[var(--color-text-muted)] max-w-sm" }, e != null && e.configured ? "Connect a valid token above to see your recordings." : "Connect your Plaud account above to see your recordings, play audio, and read transcripts.")));
  }
  t.registerWindow("plaud.main", I);
}
export {
  M as default,
  M as register
};

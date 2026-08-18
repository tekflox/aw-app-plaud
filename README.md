# Plaud

Browse your [Plaud](https://web.plaud.ai) AI recorder account (Note /
NotePin / Note Pro) from inside the workspace: see every recording, play or
download the original audio, and read the transcript and AI-generated
summary. Also exposes the same data as 5 MCP tools so an agent can pull a
recording's content into whatever it's working on (e.g. importing notes into
Notion).

Ported from agentic-workspace's `src/mcp/aw_plaud.py` + `skills/aw-plaud/`
(the tool logic and its two gotchas — a browser-shaped User-Agent, and no
token refresh — carried over unchanged) plus the paste-a-token form from
`src/app/src/components/IntegrationsTab.jsx`. The recordings view itself
(list/play/download/transcript/summary) is new — the monolith never had one.

## What It Does

- **Apps → Plaud window** — a recordings list (name, date, duration,
  transcribed/summarized badges), an in-window audio player, a download
  button for the original file, and the transcript (timestamped) and AI
  summary rendered on screen.
- **Settings → Plaud** — the canonical place to manage the connection: a
  direct link to `web.plaud.ai`, tolerant paste (bare token, with/without
  `Bearer `, or the whole `authorization: ...` line copied from DevTools),
  immediate re-validation on save, and the token's expiry shown right there.
  The connection panel inside the app window (below) still works as a
  shortcut to the same field — both save through the same `/settings` route
  and the same secret, neither duplicates the other's logic.
- **A connection panel that treats "token expired" as a first-class state**
  — not just a 401 recordings quietly fail on. Shows connected (with the
  account email), expiring soon, or expired, right in the window.
- **5 MCP tools** (`aw-plaud` server): `plaud_status`, `plaud_list_recordings`,
  `plaud_get_transcript`, `plaud_get_summary`, `plaud_download_audio`.

## Why Use It

Plaud has no supported public API for reading existing recordings (the
Embedded API is upload-only; the OAuth API is still private beta) — this app
talks to the same unofficial `api.plaud.ai` endpoints `web.plaud.ai` itself
uses, so recordings made on a Plaud device are reachable from the workspace
without exporting them by hand.

## How To Use It

1. Install the app, open **Settings → Plaud** (or the shortcut inside
   **Apps → Plaud**).
2. Follow the link to `web.plaud.ai`, log in, open DevTools → Network, and
   copy the `authorization` header value from any `api.plaud.ai` request —
   the whole header line works too, no need to trim it by hand.
3. Paste it and Save. The panel re-checks the token against Plaud
   immediately and shows the real result (connected, with the account email,
   or the actual error) instead of a blind "saved".
4. Browse recordings, play/download audio, read transcripts and summaries
   from **Apps → Plaud**.

The token is a short-lived JWT (~24h) with **no refresh flow** — when it
expires, both the Settings panel and the in-app connection panel show
"expired" (with how long ago) and prompt for a fresh paste. See
`skills/aw-plaud/SKILL.md` for the full obtain/refresh steps and the
reverse-engineered API shape.

The token is stored in the workspace's encrypted secret store
(`secrets:own`), never in plain config.

## Next step (out of scope for this version)

The real annoyance here is the daily re-paste. Two ways out, neither
implemented yet:

1. **Unofficial email+password login flow** (used by `plaud-toolkit` /
   `openplaud`) — trades the DevTools copy-paste for a ~300-day refresh
   token, at the cost of depending on another unofficial, reverse-engineered
   flow.
2. **Migrate to the official OAuth API** once Plaud's private beta opens up —
   check `dev.plaud.ai` account status periodically. This is the flow worth
   waiting for; it's the one Plaud will actually support.

## Development

```bash
cd ui && npm install && npm run build   # rebuild ui/dist/plaud-ui.mjs after any plugin.jsx change
python3 tests/validate_manifest.py aw-app.json
python3 -m pytest tests/
```

See `skills/aw-plaud/SKILL.md` for the Plaud-specific tool/API reference, and
`aw-app-template`'s `skills/aw-create-app/SKILL.md` for the general
manifest/tier/capability model this app was built against.

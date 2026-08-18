---
name: aw-plaud
description: Personal Plaud account (AI recorder — Note/NotePin/Note Pro) connector, exposed as the aw-plaud MCP server by aw-app-plaud. Covers the tool reference (list recordings, get transcript/summary, download audio), the Apps › Plaud UI (list/play/download/transcript/summary), how to obtain/refresh the bearer token (unofficial web API — official OAuth API is still private beta), and known limitations. Use whenever asked to pull Plaud recordings/transcripts, import Plaud notes somewhere (e.g. into Notion), or configure/debug the Plaud integration.
---

# aw-plaud — Plaud connector

Personal [Plaud](https://web.plaud.ai) account access via `aw-app-plaud`, a
Tier-1 aw-workspace app. Ported 2026-08-18 from agentic-workspace's
`src/mcp/aw_plaud.py` (a stdio MCP built 2026-07-17) — the tool logic and
every gotcha below carried over unchanged; only the transport moved (see
"What changed from the monolith" at the bottom).

Live exploration back then confirmed the (unofficial) endpoints work
end-to-end: list → detail → transcript (S3, gzip) → summary (S3, gzip) →
raw audio download.

## Why unofficial

Plaud's real developer platform (`dev.plaud.ai` / `docs.plaud.ai`) exists but:

- **Plaud Embedded** (the public part) is upload-and-transcribe only — no
  endpoint to fetch *existing* recordings.
- **Plaud OAuth API** (the one that would fetch existing notes/transcripts,
  read-only, scoped per account) is in **private beta** — request access at
  dev.plaud.ai, no ETA.

Until that beta access lands, `aw-plaud` talks directly to the same
`api.plaud.ai` REST API the `web.plaud.ai` frontend uses, authenticated with
a bearer token lifted from a logged-in browser session. This is the same
approach community projects (`openplaud`, `plaud-toolkit`) use — reverse
engineered, not supported by Plaud.

## Tool reference (MCP)

Through the gateway: `mcp__aw-gateway__aw__plaud__<tool>`.

| Tool | Args | What it does |
|---|---|---|
| `plaud_status` | — | Checks the configured token is set and still valid (calls `/user/me`) |
| `plaud_list_recordings` | `limit` (default 20), `skip` (default 0) | Lists recordings: id, filename, duration, start_time, `is_transcribed`, `has_summary` |
| `plaud_get_transcript` | `file_id` | Full transcript text with `[Ns]` timestamps. Fails if not transcribed yet |
| `plaud_get_summary` | `file_id` | AI-generated summary markdown. Fails if none generated yet |
| `plaud_download_audio` | `file_id`, `dest_path` (optional) | Downloads original audio (mp3/ogg/opus) to disk. Defaults to `.tmp/plaud/downloads/{file_id}.audio` |

`file_id` comes from `plaud_list_recordings`. Always check `is_transcribed` /
`has_summary` before calling `plaud_get_transcript` / `plaud_get_summary` —
freshly recorded files often haven't finished processing.

## The UI — Apps › Plaud

Unlike the monolith (which only had a paste-a-token settings form), this app
ships a real recordings view: open **Apps › Plaud** for a list of recordings
(name, date, duration, transcribed/summarized badges), an in-window audio
player, a download button for the original file, and the transcript
(timestamped) and summary rendered on screen. Play/download stream through
this app's own backend (`GET /recordings/{id}/audio`) so the bearer token
never reaches the browser.

The same window's connection panel shows the token's state plainly —
connected (with the account email), expiring soon, or expired — instead of
letting recordings quietly 401. See "Known API shape" below for why this can
be shown proactively: the JWT's own `exp` claim is decoded (unverified,
client-side hint only) as soon as a token is saved.

## Obtaining / refreshing the token

The token is a short-lived JWT (~24h TTL observed) tied to a `web.plaud.ai`
browser session — there is no refresh flow implemented, it must be
re-pasted when it expires (`plaud_status` / any tool call returns a clear
"401 — re-paste a fresh token" error when this happens, and the UI's
connection panel goes red at that point too).

**Configure via Apps → Plaud → connection panel** (paste the token, Save —
stored in this app's secret store, never in plain config).

Steps to get a fresh token:

1. Log into `web.plaud.ai` in a normal browser (Google/Apple/email SSO all work).
2. Open DevTools (F12) → Network tab.
3. Click any recording to trigger an API call.
4. Find a request to `api.plaud.ai` → Request Headers → copy the
   `authorization` value (`Bearer eyJ...`).
5. Paste into Apps → Plaud → connection panel → Save.

Only `authorization: Bearer ...` and a browser-shaped `user-agent` header
are needed — no `x-pld-user` / device-id headers required, confirmed by
testing. **Cloudflare in front of `api.plaud.ai` blocks a default HTTP
client's user-agent as a bot signature (HTTP 403 / error 1010)** — this is
why every request (including the MCP tools and the audio-streaming route)
sends an explicit Chrome-shaped `user-agent`. If you ever touch this app's
`plaud_app/client.py`, keep that header or you'll get cryptic 403s that look
like an auth problem but aren't.

## Known API shape (undocumented, reverse-engineered)

- `GET /file/simple/web?skip=&limit=&is_trash=0&sort_by=start_time&is_desc=true`
  → `{data_file_list: [...]}`, each with `id`, `filename`, `duration` (ms),
  `start_time` (ms epoch), `is_trans`, `is_summary`.
- `GET /file/detail/{id}` → `{data: {content_list: [...]}}`. Each
  `content_list` item has `data_type` (`transaction` = transcript,
  `auto_sum_note` = AI summary, `mark_memo` = highlights, `high_light` =
  notes) and `data_link` = a **presigned S3 URL, 5-minute expiry** to a
  `.json.gz` (transcript) or `.md.gz` (summary) — fetch it immediately,
  don't cache the URL.
- `GET /file/download/{id}` → raw audio bytes directly (no redirect), with
  just the bearer token — this one is *not* presigned-S3, it's a direct
  authenticated stream. `plaud_app/client.py`'s `audio_stream` checks the
  response's `content-type` before ever streaming it back as media — this
  workspace has shipped the "downloaded a 401 error body and called it
  audio" bug before (see the aw-app-plaud Kanban card that requested this
  app), so a non-audio content-type is refused rather than passed through.
- `GET /user/me` → `{data_user: {email, nickname, ...}}` — note the key is
  `data_user`, not `data` (easy bug to reintroduce).

## Known limitations / non-goals

- No write/import-to-Plaud, no delete, no folder/tag management — read-only
  by design (matches the use case: pull recordings out, work on them
  elsewhere, e.g. import into Notion).
- **No auto-refresh of the token — this is the real problem, not a
  cosmetic one.** The JWT lasts ~24h; every day it needs re-pasting or every
  tool call and the whole UI start 401ing. Two ways out exist and are
  deliberately NOT implemented yet (see the app's README "Next step"
  section for the full writeup):
  1. the unofficial email+password login flow used by `plaud-toolkit`/
     `openplaud` (~300-day refresh token, separate credential from this
     short-lived API JWT);
  2. migrate to the official OAuth API once beta access is granted — check
     `dev.plaud.ai` account status periodically.
- The shared Playwright browser (`aw-browser` container, CDP :9223) is
  genuinely multi-tenant — other concurrent sessions can steal/close tabs
  mid-flow. If you need a human to complete an interactive login there
  (e.g. Google SSO popup), expect tab collisions.

## What changed from the monolith

The tool logic is unchanged — same endpoints, same gotchas, same 5 tools.
What moved:

- **Transport**: stdio subprocess reading `PLAUD_BEARER_TOKEN` from its own
  environment → Streamable HTTP server in-process with the rest of this app,
  reading the token from `ctx.secrets` (the workspace's per-app encrypted
  store) on every call. Same reasoning as `aw-app-notion`'s `aw-kanban`
  server: a stdio subprocess spawned by a sibling gateway container has no
  path to a credential that lives in this process's own secret store.
- **Config surface**: `Settings → AW → MCPs → aw-plaud` (raw JSON editor) →
  Apps → Plaud's own connection panel (`POST /api/apps/plaud/settings`).
- **New**: the recordings UI itself. The monolith had no way to view a
  recording at all — only the bare token-paste form
  (`PlaudIntegration` in `src/app/src/components/IntegrationsTab.jsx`).

## Related

- `skills/aw-google-workspace/` — if the eventual import target is Google
  Docs instead of Notion.
- `skills/aw-notion/` / `skills/aw-kanban/` — if the eventual import target
  is Notion.

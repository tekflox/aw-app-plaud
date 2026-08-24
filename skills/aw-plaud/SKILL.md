---
name: aw-plaud
description: Personal Plaud account (AI recorder — Note/NotePin/Note Pro) connector, exposed as the aw-plaud MCP server by aw-app-plaud. Covers the tool reference (list recordings, get transcript/summary, download audio), the Apps › Plaud UI (list/play/download/transcript/summary), the three ways to connect (the recommended official-CLI login flow, this app's own dormant/waitlisted OAuth client, and the pasted-bearer fallback), and known limitations. Use whenever asked to pull Plaud recordings/transcripts, import Plaud notes somewhere (e.g. into Notion), or configure/debug the Plaud integration.
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

## Why unofficial (the pasted-bearer path, still the fallback)

Plaud's real developer platform (`dev.plaud.ai` / `docs.plaud.ai`) exists but
is two separate products, easy to conflate:

- **Plaud Embedded** (`docs.plaud.ai/plaud-embedded`, GA) — a partner
  provisions and binds *new* users/devices to their own app
  (`POST {region}/developer/api/oauth/partner/access-token` with
  client_id+secret_key, then `.../open/partner/users/access-token` per
  user). No path from this API to an *existing* personal account's own
  recordings — it's for building a product on top of Plaud hardware, not
  connecting an account that already has notes in it.
- **Plaud OAuth API** (browser consent, an existing user authorizing a
  third party to read their *own* recordings — the one this app actually
  needs) is a separate product, still in **private beta**
  (support.plaud.ai/hc/en-us/articles/56061278749209) — waitlist-only, and
  per Plaud's own FAQ the endpoint details aren't published even to
  accepted beta members yet ("provided once the API is available").

Until that lands, `aw-plaud` talks directly to the same `api.plaud.ai` REST
API the `web.plaud.ai` frontend uses, authenticated with a bearer token
lifted from a logged-in browser session. This is the same approach
community projects (`openplaud`, `plaud-toolkit`) use — reverse engineered,
not supported by Plaud.

## Three ways to connect

Settings → Plaud shows all three, in this order:

1. **Official-CLI login (recommended)** — this app runs the real, first-party
   `@plaud-ai/mcp` CLI *inside its own container* and orchestrates its login.
   See "The official-CLI login flow" below.
2. **This app's own OAuth client** (`plaud_app/oauth.py`) — dormant until
   Plaud hands out real credentials off their OAuth API waitlist. See "Own
   OAuth client" below.
3. **Pasted bearer token** — the original fallback, unchanged. See
   "Obtaining / refreshing the token" below.

All three ultimately feed the same `PlaudClient` (`plaud_app/client.py`) the
same way: a bearer token good for `api.plaud.ai`. `routes.py`'s
`_combined_token_getter` tries them in the order above.

## The official-CLI login flow (recommended)

**This was previously believed impossible from inside this workspace** — an
earlier version of this doc said so, reasoning that the CLI's OAuth
redirect_uri (`http://localhost:8199/auth/callback`) is a loopback address
no external browser could ever reach. That's true for the *browser*, but
irrelevant for who actually needs to reach it: **the process that owns
`localhost:8199` only needs to be the process running the login, not the
browser.** So this app runs the CLI itself, in its own container, where
`localhost:8199` is real and reachable — see `plaud_app/cli_login.py`'s
module docstring for the full mechanism, discovered 2026-08-24 by reading
the CLI's own bundle (`npm pack @plaud-ai/mcp` + read `dist/*.js`).

The 4 things that make this work, briefly (full detail in `cli_login.py`):

1. **2-minute window.** The CLI's own login step times out after 120s and
   mints a fresh PKCE pair on every attempt — `start()` always kills
   whatever attempt is currently running first, so a stale link is never
   reusable.
2. **`~/.plaud/tokens-mcp.json` survives a container recreation** because
   the CLI runs with `HOME` pointed at `$AW_WORKSPACE_HOME/data/plaud/cli-home`
   — durable, and ours.
3. **The installer's `~/.claude` side effect (MCP registration + 7 skills)
   never touches the real workspace home** — same `HOME` override contains
   it. Rather than fake "Claude Code" detected (which would shell out to a
   real `claude mcp add`, dependent on that binary being on PATH in
   whatever process runs this app, and would trigger the skills copy), an
   empty `.cursor/` dir is pre-seeded instead: it satisfies the installer's
   "at least one local client detected" gate with a dependency-free plain
   JSON-file write, confirmed live against the real CLI 2026-08-24.
4. **npx/npm cache** is pointed at this app's own
   `$AW_WORKSPACE_HOME/data/plaud/npm-cache`, never the shared `$HOME/.npm`
   (root-owned cache entries there break every later install with EACCES —
   see `app-npm-install-eacces-home-cache` in the KB).

**The one step only a human can do:** open the authorize link Settings
shows, approve in your own browser, and paste back the URL the browser
lands on afterwards — a connection-refused page, since it's redirecting to
`localhost:8199` on *your* machine, not this workspace's. That's expected,
and the panel says so before it happens. Pasting it lets this app extract
`code`/`state` and GET the CLI's own callback server with them; the
subprocess does the actual token exchange.

**Refresh** after that is this app's own responsibility (`cli_login.
get_valid_access_token`), since the CLI subprocess isn't kept running — it
POSTs `https://platform.plaud.ai/developer/api/oauth/third-party/access-token/refresh`
with just `refresh_token` (no client_id/secret needed on refresh, confirmed
from the CLI's own bundle). That endpoint — along with the exchange one the
CLI subprocess itself uses — is the real "Plaud OAuth API" `oauth.py`'s own
docstring describes as unpublished; the CLI's bundle is what published it,
for its own first-party `client_id` only. Not verified against a real
completed refresh yet (no live token to test with as of this writing).

**Unverified end-to-end:** completing a real browser authorization needs a
human, which building this flow did not include. If `plaud_status` doesn't
report connected after a real completed login, the most likely cause is
that a CLI-issued token isn't valid for `api.plaud.ai`'s endpoints (a
different auth realm than `platform.plaud.ai/developer/api`, which is what
the CLI itself actually calls) — check that first.

## Own OAuth client (waitlisted)

`aw-app-plaud` is also, separately, its own OAuth client (`plaud_app/oauth.py`):
PKCE authorization-code flow, its own public callback
(`/api/apps/plaud/oauth/callback`, built from `AW_WORKSPACE_API_URL`),
transparent refresh, and revocation handling (a failed refresh with
`invalid_grant` clears the stored tokens instead of retry-looping).

The catch: `plaud_client_id` / `plaud_client_secret` /
`plaud_oauth_authorize_url` / `plaud_oauth_token_url` (`config_schema` in
`aw-app.json`, the last two also x-secret-adjacent — see the app itself)
are **empty by design** — Plaud hasn't published them to third parties (the
official-CLI flow above discovered the real URLs, but they're only usable
by the CLI's own first-party `client_id`+fixed `localhost:8199` redirect —
this app still has no client_id of its own). The Settings panel shows
exactly which of the 4 are missing. Nothing to guess or hardcode: once
Plaud hands out real values off the OAuth API waitlist, paste them in and
Connect starts working with no code change. Prefer the CLI flow above until
then.

The pasted-bearer flow below remains the fallback for as long as neither of
the above is connected — do not remove it.

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
- **The pasted-bearer path still has no auto-refresh** — the JWT lasts
  ~24h and needs re-pasting, or every tool call and the whole UI start
  401ing. This is now the last-resort FALLBACK, not the only option: the
  official-CLI login flow (recommended) and this app's own OAuth client
  (waitlisted) both refresh automatically once connected.
- **The official-CLI login needs a human for the browser step, once.**
  There's no way around this — Plaud's consent screen needs a real logged-in
  browser. It's a one-time cost per connection (refresh after that is
  automatic), not a recurring one like the pasted-bearer path.
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

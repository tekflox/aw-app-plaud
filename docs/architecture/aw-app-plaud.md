---
repo: architecture
path: docs/architecture/aw-app-plaud.md
source: generated
edited: false
checksum: sha256:3f9a9228c1f2150733522d1cc0a0e3a0877e6aa657491bd19f9716e71fb41b9e
---
# Plaud

- **repo**: aw-app-plaud
- **layer**: app
- **technologies**: python, react
- **health** (derived): planned

Browse your Plaud AI recorder notes without leaving the workspace: see every recording, play or download the original audio, and read the transcript and AI summary — plus the 5 aw-plaud MCP tools an agent can use to pull the same data.

## Connections
- `http` → **aw-workspace** — routes mounted at /api/apps/plaud
- `stdio-mcp` → **mcp-gateway** — MCP surface aggregated by the gateway

## MCP tools
- `plaud_download_audio`
- `plaud_get_summary`
- `plaud_get_transcript`
- `plaud_list_recordings`
- `plaud_status`

## Requirements
_none documented_

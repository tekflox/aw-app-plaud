"""The ``aw-plaud`` MCP server, over Streamable HTTP (``POST /mcp``).

Ported from agentic-workspace's ``src/mcp/aw_plaud.py`` (a stdio server) —
all 5 tools, unabridged: ``plaud_status``, ``plaud_list_recordings``,
``plaud_get_transcript``, ``plaud_get_summary``, ``plaud_download_audio``.
Handlers call :class:`~plaud_app.client.PlaudClient` directly, in-process,
using the token from this app's own secret store — no env var, no second
hop.

``plaud_download_audio`` still writes to local disk (``.tmp/plaud/downloads/``
by default) and returns the path, exactly like the original — that is the
contract agents already know (``[[ATTACH: path]]`` / ``Read``). The UI's own
play/download instead streams through ``routes.py``'s ``/recordings/{id}/audio``
and ``/download`` — a browser has no access to this process's filesystem.
"""
from __future__ import annotations

import json
import logging
import os

from fastapi.concurrency import run_in_threadpool

from ..client import PlaudClient, PlaudError

log = logging.getLogger("aw_apps.plaud.mcp")

SERVER_NAME = "aw-plaud"
SERVER_VERSION = "1.0.0"

DOWNLOAD_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".tmp", "plaud", "downloads",
)


def _ok(req_id, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": False}}


def _err(req_id, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": True}}


TOOLS_SCHEMA: list[dict] = [
    {
        "name": "plaud_status",
        "description": (
            "Check whether a Plaud token is configured and still valid. Call "
            "this first if anything else fails — a 401 from any other tool "
            "means the token expired and needs re-pasting (see the aw-plaud "
            "skill)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "plaud_list_recordings",
        "description": (
            "List recent Plaud recordings — id, filename, duration, start "
            "time, and whether it's been transcribed (is_transcribed). Use "
            "the id with plaud_get_transcript / plaud_get_summary / "
            "plaud_download_audio."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "skip": {"type": "integer", "description": "Offset for pagination (default 0)"},
            },
        },
    },
    {
        "name": "plaud_get_transcript",
        "description": (
            "Full transcript text for one recording, with timestamps. Fails "
            "if the recording hasn't finished transcribing yet (check "
            "is_transcribed from plaud_list_recordings)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "Recording id from plaud_list_recordings"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "plaud_get_summary",
        "description": (
            "AI-generated summary (markdown) for one recording. Fails if no "
            "summary has been generated yet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "Recording id from plaud_list_recordings"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "plaud_download_audio",
        "description": (
            "Download the original audio (mp3/ogg/opus) for one recording "
            "to local disk. Returns the local file path — use it with "
            "[[ATTACH: path]] or Read for further processing. Defaults to "
            ".tmp/plaud/downloads/."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "Recording id from plaud_list_recordings"},
                "dest_path": {"type": "string", "description": "Optional absolute path to save to"},
            },
            "required": ["file_id"],
        },
    },
]


def _h_status(client: PlaudClient, args: dict) -> str:
    st = client.status()
    if not st.configured:
        return ("PLAUD token is not configured. Configure it in Apps → Plaud "
                "(see the aw-plaud skill for how to obtain one).")
    if st.valid:
        return f"Connected as {st.email or '(unknown user)'}."
    if st.expired:
        return ("Plaud token has expired (401). Re-paste a fresh token in "
                "Apps → Plaud (see the aw-plaud skill).")
    return f"Plaud status check failed: {st.error}"


def _h_list(client: PlaudClient, args: dict) -> str:
    recordings = client.list_recordings(limit=args.get("limit") or 20, skip=args.get("skip") or 0)
    if not recordings:
        return "No recordings found."
    lines = [
        f"{r['id']} | {r['filename']} | {r['duration_s']}s | "
        f"start={r['start_time_ms']} | is_transcribed={r['is_transcribed']} | "
        f"has_summary={r['has_summary']}"
        for r in recordings
    ]
    return "\n".join(lines)


def _h_transcript(client: PlaudClient, args: dict) -> str:
    file_id = args.get("file_id") or ""
    if not file_id:
        raise ValueError("file_id is required.")
    segments = client.get_transcript(file_id)
    return "\n".join(f"[{s['start_s']}s] {s['text']}" for s in segments)


def _h_summary(client: PlaudClient, args: dict) -> str:
    file_id = args.get("file_id") or ""
    if not file_id:
        raise ValueError("file_id is required.")
    return client.get_summary(file_id)


def _h_download(client: PlaudClient, args: dict) -> str:
    file_id = args.get("file_id") or ""
    if not file_id:
        raise ValueError("file_id is required.")
    dest_path = args.get("dest_path")
    if not dest_path:
        os.makedirs(DOWNLOAD_DIR_DEFAULT, exist_ok=True)
        dest_path = os.path.join(DOWNLOAD_DIR_DEFAULT, f"{file_id}.audio")
    resp, http_client = client.audio_stream(file_id)
    try:
        total = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
                total += len(chunk)
    finally:
        resp.close()
        http_client.close()
    return f"Downloaded {total} bytes to {dest_path}"


HANDLERS = {
    "plaud_status": _h_status,
    "plaud_list_recordings": _h_list,
    "plaud_get_transcript": _h_transcript,
    "plaud_get_summary": _h_summary,
    "plaud_download_audio": _h_download,
}


async def handle_request(request: dict, *, client: PlaudClient) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_SCHEMA}}
    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"}}

    name = request.get("params", {}).get("name", "")
    args = request.get("params", {}).get("arguments", {}) or {}
    handler = HANDLERS.get(name)
    if not handler:
        return _err(req_id, f"Unknown tool: {name}")

    try:
        result = await run_in_threadpool(handler, client, args)
    except ValueError as exc:
        return _err(req_id, str(exc))
    except PlaudError as exc:
        return _err(req_id, f"{name} failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - last resort, must not 500 the route
        log.exception("plaud MCP tool %s failed", name)
        return _err(req_id, f"{name} failed: {exc}")

    return _ok(req_id, result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))

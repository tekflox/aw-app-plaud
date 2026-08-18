#!/usr/bin/env python3
"""Validates aw-app.json — schema, referenced files, and the things that only
blow up on a REAL install.

**The canonical copy lives in aw-marketplace** (`scripts/validate_manifest.py`
+ `schemas/aw-app.schema.json`), and `app-release.yml` runs THAT against every
app it releases. This copy is for running the same checks locally before you
push:

    python3 tests/validate_manifest.py aw-app.json

See aw-app-template's tests/validate_manifest.py (this file's origin) for
the full rationale.
"""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parent.parent

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_schema_flag = None
if "--schema" in sys.argv:
    _schema_flag = Path(sys.argv[sys.argv.index("--schema") + 1]).resolve()

MANIFEST_PATH = Path(_args[0]).resolve() if _args else Path("aw-app.json").resolve()
APP_ROOT = MANIFEST_PATH.parent


def _resolve_schema() -> Path:
    if _schema_flag:
        return _schema_flag
    local = ROOT / "schemas" / "aw-app.schema.json"
    if local.is_file():
        return local
    sibling = ROOT.parent / "aw-marketplace" / "schemas" / "aw-app.schema.json"
    if sibling.is_file():
        return sibling
    raise SystemExit(
        "no schema found. This repo has no schemas/aw-app.schema.json and "
        "aw-marketplace is not checked out next to it — pass one explicitly:\n"
        "    python3 tests/validate_manifest.py aw-app.json --schema <path>"
    )


SCHEMA_PATH = _resolve_schema()

KNOWN_WIDGETS = {
    "markdown", "list", "button", "iframe", "app_iframe",
    "collapsible", "form", "auth_status",
}
KNOWN_FORM_INPUTS = {"text", "password", "select", "checkbox"}
DEGRADES_TO_TEXT = {"number", "email", "url", "tel", "date"}

errors: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


manifest = json.loads(MANIFEST_PATH.read_text())
schema = json.loads(SCHEMA_PATH.read_text())
jsonschema.validate(instance=manifest, schema=schema)

contributes = manifest.get("contributes", {}) or {}

for cli in contributes.get("system_clis", []) or []:
    if not (APP_ROOT / cli["installer"]).is_file():
        fail(f"installer script missing: {cli['installer']}")

frontend = contributes.get("frontend") or {}
bundle = frontend.get("bundle")
if bundle and not (APP_ROOT / bundle).is_file():
    fail(f"frontend bundle missing: {bundle} — run `npm run build` in ui/ AND "
         f"commit the result (release CI does not build it; check that "
         f".gitignore does not exclude it)")

for skill in contributes.get("skills", []) or []:
    if not (APP_ROOT / skill["path"]).is_file():
        fail(f"skill file missing: {skill['path']}")

for _kind, _field in (("agents", "system_prompt_file"), ("groups", "instructions_file")):
    for entry in ((contributes.get("agents") or {}).get(_kind) or []):
        ref = entry.get(_field)
        if ref and not (APP_ROOT / ref).is_file():
            fail(f"contributes.agents.{_kind}[{entry.get('slug')!r}]: {_field} "
                 f"{ref!r} does not exist — the object is still created, with "
                 f"that field empty.")

migrations = manifest.get("migrations") or {}
if migrations:
    mig_dir = APP_ROOT / (migrations.get("dir") or "migrations")
    if not mig_dir.is_dir():
        fail(f"migrations.dir declared but missing: {mig_dir.name}/")

windows = contributes.get("windows", []) or []
declarative_window_ids = set()


def check_widgets(widgets, where: str) -> None:
    for widget in widgets or []:
        wtype = widget.get("type")
        if wtype not in KNOWN_WIDGETS:
            fail(f"{where}: widget type {wtype!r} is not implemented by the "
                 f"declarative renderer — it will render nothing. "
                 f"Supported: {', '.join(sorted(KNOWN_WIDGETS))}.")
            continue
        if wtype == "collapsible":
            check_widgets(widget.get("widgets"), f"{where} > collapsible")
        if wtype == "list" and widget.get("bind"):
            fail(f"{where}: `list` renders STATIC items from the spec and has "
                 f"no `bind` — use an `iframe` onto your own route instead.")
        if wtype == "form":
            for field in widget.get("fields", []) or []:
                itype = field.get("input", "text")
                if itype in DEGRADES_TO_TEXT:
                    warn(f"{where}: form input {itype!r} renders as a plain "
                         f"text box.")
                elif itype not in KNOWN_FORM_INPUTS:
                    fail(f"{where}: form input {itype!r} is not implemented.")


for win in windows:
    body = win.get("body", {}) or {}
    if body.get("type") != "declarative":
        continue
    declarative_window_ids.add(win["id"])
    spec_path = APP_ROOT / body["spec"]
    if not spec_path.is_file():
        fail(f"window spec missing: {body['spec']}")
        continue
    spec = json.loads(spec_path.read_text())
    for region in spec.get("regions", []) or []:
        check_widgets(region.get("widgets"), f"{body['spec']} region {region.get('id')!r}")

window_ids = {w["id"] for w in windows}
for panel in contributes.get("settings_panels", []) or []:
    target = panel.get("window")
    if not target:
        continue
    if target not in window_ids:
        fail(f"settings_panel {panel['id']!r} points at unknown window {target!r}")
    elif target not in declarative_window_ids:
        fail(f"settings_panel {panel['id']!r} points at window {target!r}, which "
             f"is not `declarative`.")

for w in warnings:
    print(f"WARN: {w}", file=sys.stderr)

if errors:
    for e in errors:
        print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)

print("OK: aw-app.json is valid, every referenced file exists, and all "
      "declarative widgets are ones the renderer implements")

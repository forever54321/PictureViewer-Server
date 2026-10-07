"""Add (or update) a shared backup folder that phones can pick in the app.

    python add_folder.py                      asks for a name and a folder path
    PV_FOLDER_NAME=... PV_FOLDER_PATH=... python add_folder.py   (non-interactive)

The folder is saved to folders.json next to the server's config (the same
file the running server reads live, so it appears in the app shortly — no
restart needed). Dangerous folders (a whole drive, your user folder, system
folders, the server's own folder) are refused, and folders.json is written
atomically so a crash can never leave it half-written.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config  # noqa: E402  (honours PICTUREVIEWER_CONFIG; no side effects)
import safety  # noqa: E402

FOLDERS_FILE = config.FOLDERS_FILE


def load_folders() -> dict:
    try:
        with open(FOLDERS_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def add_folder(name: str, path: str):
    """Returns (ok, message)."""
    name = (name or "").strip()
    path = os.path.expandvars(os.path.expanduser(safety.unquote_path(path)))
    if not name or not path:
        return False, "A name and a folder path are both required. Nothing was saved."
    if name == "Library":
        return False, "'Library' is reserved for the main folder — pick another name."
    if any(ord(c) < 32 for c in name) or len(name) > 80:
        return False, "That name isn't valid (max 80 characters, no control characters)."
    path = os.path.abspath(path)
    problem = safety.root_problem(path, app_dirs=config.app_dirs())
    if problem:
        return False, f"Refused: {problem}"
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return False, f"Could not create the folder '{path}': {e}"
    data = load_folders()
    existed = name in data
    data[name] = path
    config.write_json_atomic(FOLDERS_FILE, data)
    verb = "Updated" if existed else "Added"
    return True, f"{verb} backup folder '{name}' -> {path}"


def main():
    name = os.environ.get("PV_FOLDER_NAME")
    path = os.environ.get("PV_FOLDER_PATH")
    if name is None and path is None and sys.stdin.isatty():
        print("Add a backup folder (each phone can pick its own folder in the app).")
        name = input("  Name to show in the app (e.g. Wife's iPhone): ")
        path = input("  Full folder path (e.g. D:\\Backups\\Wife): ")
    ok, msg = add_folder(name or "", path or "")
    print("  " + msg)
    if ok:
        print("  It will appear in the app shortly (no restart needed).")
        print("  All your extra shared folders:")
        for n, p in load_folders().items():
            print(f"    - {n}: {p}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

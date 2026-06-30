"""Add (or update) a shared backup folder that phones can pick in the app.

Reads the folder name + path from the PV_FOLDER_NAME / PV_FOLDER_PATH
environment variables (set by add_folder.bat so special characters survive),
creates the folder if needed, and saves it to folders.json next to this file.
The running server reads folders.json live, so the new folder shows up in the
app the next time it refreshes — no restart needed.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDERS_FILE = os.path.join(HERE, "folders.json")


def main():
    name = (os.environ.get("PV_FOLDER_NAME") or "").strip().strip('"')
    path = (os.environ.get("PV_FOLDER_PATH") or "").strip().strip('"')

    if not name or not path:
        print("  A name and a folder path are both required. Nothing was saved.")
        return 1

    # Create the folder if it doesn't exist yet.
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        print(f"  Could not create the folder '{path}': {e}")
        return 1

    data = {}
    if os.path.exists(FOLDERS_FILE):
        try:
            with open(FOLDERS_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    existed = name in data
    data[name] = path
    with open(FOLDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    verb = "Updated" if existed else "Added"
    print(f"  {verb} backup folder '{name}'  ->  {path}")
    print("  It will appear in the app shortly (no restart needed).")
    print("  All your shared folders:")
    for n, p in data.items():
        print(f"    - {n}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

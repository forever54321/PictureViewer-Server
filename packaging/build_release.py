#!/usr/bin/env python3
"""Build the three release downloads from this repository:

    dist/lumina-server-windows-<ver>.zip
    dist/lumina-server-macos-<ver>.zip
    dist/lumina-server-linux-<ver>.tar.gz

Only an explicit allowlist of files is packaged (never .git, tests, venv,
logs, config.json, certs, .env, folders.json, ._* or __pycache__, nor the old
installer/ app bundles). Shell entry points keep their executable bit in both
the zips and the tarball. Archives are reproducible: timestamps come from
SOURCE_DATE_EPOCH, else the last git commit, and entries are sorted.
Prints a JSON summary with each file's size and SHA-256.
"""
from __future__ import annotations

import fnmatch
import glob
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

COMMON = ["server.py", "config.py", "safety.py", "lumina_admin.py", "add_folder.py", "run.py",
          "requirements.txt", "requirements-heic.txt", "README.md", "PRIVACY_POLICY.md",
          "REMOTE_ACCESS.md", "VERSION", "LICENSE"]
PLATFORM = {
    "windows": ["install_windows.bat", "install_windows.ps1", "add_folder.bat", "windows/*.ps1"],
    "macos": ["install_mac.command", "unix/*.sh"],
    "linux": ["install_linux.sh", "unix/*.sh"],
}
REQUIRED = {"windows": "install_windows.bat", "macos": "install_mac.command", "linux": "install_linux.sh"}
NEVER = ["._*", "*.pyc", ".env", "config.json", "folders.json", "secret.key", "*.pem", "*.log",
         ".DS_Store"]
NEVER_DIRS = {".git", "tests", "venv", "logs", "certs", "__pycache__", ".thumbnails", "installer",
              "dist", "packaging"}
EXEC_SUFFIXES = (".sh", ".command")


def build_epoch() -> int:
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        out = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%ct"],
                             capture_output=True, text=True, check=True).stdout.strip()
        return int(out)
    except Exception:
        return int(time.time())


def collect(platform: str) -> list[str]:
    files = []
    for pattern in COMMON + PLATFORM[platform]:
        matches = sorted(glob.glob(os.path.join(ROOT, pattern)))
        if not matches and pattern != "LICENSE":
            raise SystemExit(f"missing file for {platform}: {pattern}")
        for m in matches:
            rel = os.path.relpath(m, ROOT).replace(os.sep, "/")
            parts = rel.split("/")
            if any(p in NEVER_DIRS for p in parts[:-1]):
                continue
            if any(fnmatch.fnmatch(parts[-1], pat) for pat in NEVER):
                continue
            if os.path.isfile(m) and rel not in files:
                files.append(rel)
    if REQUIRED[platform] not in files:
        raise SystemExit(f"{platform}: entry point {REQUIRED[platform]} missing")
    return sorted(files)


def mode_for(rel: str) -> int:
    return 0o755 if rel.endswith(EXEC_SUFFIXES) else 0o644


def read(rel: str) -> bytes:
    with open(os.path.join(ROOT, rel), "rb") as fh:
        data = fh.read()
    if rel.endswith(EXEC_SUFFIXES) and b"\r\n" in data:
        raise SystemExit(f"{rel} has Windows line endings - shell scripts must use LF")
    return data


def build_zip(path: str, top: str, files: list[str], epoch: int):
    dt = time.gmtime(max(epoch, 315532800))[:6]   # zip can't store dates before 1980
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        d = zipfile.ZipInfo(top + "/", date_time=dt)
        d.create_system = 3
        d.external_attr = (0o40755 << 16) | 0x10
        zf.writestr(d, b"")
        for rel in files:
            info = zipfile.ZipInfo(f"{top}/{rel}", date_time=dt)
            info.create_system = 3                      # unix -> mode bits are honoured
            info.external_attr = (0o100000 | mode_for(rel)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, read(rel))


def build_tgz(path: str, top: str, files: list[str], epoch: int):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        dirs = sorted({top} | {f"{top}/{os.path.dirname(r)}" for r in files if "/" in r})
        for d in dirs:
            ti = tarfile.TarInfo(d)
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, epoch
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti)
        for rel in files:
            data = read(rel)
            ti = tarfile.TarInfo(f"{top}/{rel}")
            ti.size, ti.mode, ti.mtime = len(data), mode_for(rel), epoch
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, io.BytesIO(data))
    with open(path, "wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=epoch, compresslevel=9) as gz:
            gz.write(raw.getvalue())


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
        version = fh.read().strip()
    if not version or any(c.isspace() or c in "/\\" for c in version):
        raise SystemExit("VERSION file is empty or invalid")
    epoch = build_epoch()
    os.makedirs(DIST, exist_ok=True)
    out = {"version": version, "source_date_epoch": epoch, "artifacts": []}
    for platform, ext in (("windows", "zip"), ("macos", "zip"), ("linux", "tar.gz")):
        top = f"lumina-server-{platform}-{version}"
        path = os.path.join(DIST, f"{top}.{ext}")
        files = collect(platform)
        (build_zip if ext == "zip" else build_tgz)(path, top, files, epoch)
        out["artifacts"].append({
            "platform": platform, "file": os.path.relpath(path, ROOT),
            "size_bytes": os.path.getsize(path), "sha256": sha256(path), "files": len(files),
        })
    with open(os.path.join(DIST, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        for a in out["artifacts"]:
            fh.write(f"{a['sha256']}  {os.path.basename(a['file'])}\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

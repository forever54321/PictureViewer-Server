#!/usr/bin/env python3
"""Small admin helper used by the installers (Windows setup.ps1, macOS/Linux
install scripts) and the Start Menu Status/Add-Folder tools.

Design rules:
  * User-supplied values (folder paths, access codes) are NEVER passed on the
    command line. They arrive on STDIN as base64-encoded UTF-8 JSON, which
    survives any shell/console code page untouched.
  * Results are printed as ONE line ``@@JSON <ascii-only json>`` so callers can
    find it even if Python prints warnings.
  * There is exactly one rule set: the same safety.root_problem and
    config.validate_access_code the server itself uses.

Usage:  <payload-b64> | python lumina_admin.py <command>
Commands: env-info, gen-code, validate-code, validate-folder, read-config,
          write-config, migrate-env, show-code, info, add-folder, list-folders,
          selftest
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import safety  # noqa: E402  (stdlib-only, safe before the venv exists)

SUPPORTED_MIN = (3, 10)
SUPPORTED_MAX = (3, 14)   # newest CPython the pinned wheels are published for


def _out(obj) -> int:
    sys.stdout.write("@@JSON " + json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return 0 if obj.get("ok", True) else 1


def _payload() -> dict:
    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        return {}
    if raw.startswith("{"):
        data = json.loads(raw)       # plain JSON is accepted too
    else:
        data = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _config():
    import config  # honours PICTUREVIEWER_CONFIG
    return config


# ---------------------------------------------------------------------------

def cmd_env_info(_p):
    v = sys.version_info[:2]
    exe = getattr(sys, "_base_executable", None) or sys.executable
    base_dir = os.path.dirname(exe)
    pyw = os.path.join(base_dir, "pythonw.exe")
    import platform
    return _out({
        "ok": True,
        "version": "%d.%d.%d" % sys.version_info[:3],
        "supported": SUPPORTED_MIN <= v <= SUPPORTED_MAX,
        "machine": platform.machine(),
        "bits": 64 if sys.maxsize > 2 ** 32 else 32,
        "base_executable": exe,
        "base_pythonw": pyw if os.path.isfile(pyw) else "",
        "in_venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
    })


def generate_code(groups: int = 4, size: int = 4) -> str:
    """e.g. 'Kp7m-Qx3r-Vb9t-Wn2h' — easy to type on a phone, meets the policy."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    while True:
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(size)) for _ in range(groups))
        if (any(c.islower() for c in code) and any(c.isupper() for c in code)
                and any(c.isdigit() for c in code)):
            return code


def cmd_gen_code(_p):
    return _out({"ok": True, "code": generate_code()})


def cmd_validate_code(p):
    cfg = _config()
    reason = cfg.validate_access_code(str(p.get("code") or ""))
    return _out({"ok": reason is None, "reason": reason or ""})


def _folder_report(path: str, extra_app_dirs=()):
    cfg = _config()
    dirs = list(cfg.app_dirs()) + [d for d in extra_app_dirs if d]
    path = os.path.expandvars(os.path.expanduser(safety.unquote_path(path)))
    reason = safety.root_problem(path, app_dirs=dirs)
    resolved = os.path.realpath(os.path.abspath(path)) if path else ""
    low = resolved.lower()
    onedrive = ("onedrive" in low) or any(
        os.environ.get(v) and low.startswith(os.path.realpath(os.environ[v]).lower())
        for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"))
    icloud = "icloud" in low
    return {"ok": reason is None, "reason": reason or "", "path": resolved,
            "exists": os.path.isdir(resolved) if resolved else False,
            "cloud_synced": bool(onedrive or icloud)}


def cmd_validate_folder(p):
    return _out(_folder_report(p.get("path", ""), p.get("app_dirs") or ()))


def cmd_read_config(_p):
    cfg = _config()
    exists = os.path.isfile(cfg.CONFIG_FILE)
    data = cfg._read_json_config(cfg.CONFIG_FILE) if exists else {}
    return _out({
        "ok": True, "exists": exists, "config_file": cfg.CONFIG_FILE,
        "media_folder": data.get("media_folder", ""),
        "port": data.get("port", 8500), "https_port": data.get("https_port", 8543),
        "auto_organize": data.get("auto_organize", True),
        "organize_existing": data.get("organize_existing", True),
        "has_code": cfg.validate_access_code(str(data.get("access_code") or "")) is None,
        "has_secret": len(str(data.get("secret_key") or "")) >= 32,
        "log_dir": data.get("log_dir", ""), "cert_dir": data.get("cert_dir", ""),
        "thumbnail_dir": data.get("thumbnail_dir", ""),
    })


_ALLOWED_KEYS = {"media_folder", "access_code", "port", "https_port", "auto_organize",
                 "organize_existing", "allowed_hosts", "thumbnail_dir", "cert_dir",
                 "log_dir", "max_upload_size_mb", "host", "media_roots",
                 "thumbnail_cache_max_mb", "min_free_space_mb"}


def cmd_write_config(p):
    """Merge `values` into config.json (atomic). Generates secret_key only if
    missing/short or when regen_secret is true."""
    cfg = _config()
    path = cfg.CONFIG_FILE
    data = cfg._read_json_config(path) if os.path.isfile(path) else {}
    values = p.get("values") or {}
    unknown = set(values) - _ALLOWED_KEYS
    if unknown:
        return _out({"ok": False, "reason": "unknown setting(s): " + ", ".join(sorted(unknown))})
    data.update(values)
    for k in ("port", "https_port"):
        if k in data:
            try:
                data[k] = int(data[k])
            except (TypeError, ValueError):
                return _out({"ok": False, "reason": f"{k} must be a number"})
            if not 1024 <= data[k] <= 65535:
                return _out({"ok": False, "reason": f"{k} must be between 1024 and 65535"})
    if data.get("port") and data.get("port") == data.get("https_port"):
        return _out({"ok": False, "reason": "HTTP and HTTPS ports must differ"})
    reason = cfg.validate_access_code(str(data.get("access_code") or ""))
    if reason:
        return _out({"ok": False, "reason": "access code must contain " + reason})
    rep = _folder_report(data.get("media_folder", ""))
    if not rep["ok"]:
        return _out({"ok": False, "reason": rep["reason"]})
    data["media_folder"] = rep["path"]
    if p.get("regen_secret") or len(str(data.get("secret_key") or "")) < 32:
        data["secret_key"] = secrets.token_hex(32)
    for d in ("log_dir", "cert_dir", "thumbnail_dir"):
        if data.get(d):
            os.makedirs(data[d], exist_ok=True)
    cfg.write_json_atomic(path, data)
    return _out({"ok": True, "config_file": path})


_ENV_MAP = {
    "PICTUREVIEWER_MEDIA_FOLDER": "media_folder",
    "PICTUREVIEWER_ACCESS_CODE": "access_code",
    "PICTUREVIEWER_SECRET_KEY": "secret_key",
    "PICTUREVIEWER_PORT": "port",
    "PICTUREVIEWER_HTTPS_PORT": "https_port",
    "PICTUREVIEWER_AUTO_ORGANIZE": "auto_organize",
    "PICTUREVIEWER_ORGANIZE_EXISTING": "organize_existing",
}


def cmd_migrate_env(p):
    """Copy an old install's .env (+ folders.json + certs) into config.json.
    Existing config.json values win; nothing in the old folder is changed."""
    cfg = _config()
    env_path = str(p.get("env_path") or "")
    if os.path.isdir(env_path):
        env_path = os.path.join(env_path, ".env")
    if not os.path.isfile(env_path):
        return _out({"ok": False, "reason": "no .env file found there"})
    old_dir = os.path.dirname(os.path.abspath(env_path))
    vals = cfg.read_env_file(env_path)
    path = cfg.CONFIG_FILE
    data = cfg._read_json_config(path) if os.path.isfile(path) else {}
    migrated = []
    for env_key, json_key in _ENV_MAP.items():
        if env_key in vals and json_key not in data:
            v = vals[env_key]
            if json_key in ("port", "https_port"):
                try:
                    v = int(v)
                except ValueError:
                    continue
            if json_key in ("auto_organize", "organize_existing"):
                v = str(v).strip().lower() not in ("0", "false", "no", "off", "")
            data[json_key] = v
            migrated.append(json_key)
    # Old installs could change the port by editing config.py.
    if "port" not in data:
        try:
            import re
            with open(os.path.join(old_dir, "config.py"), encoding="utf-8") as fh:
                m = re.search(r"^PORT\s*=\s*(\d+)", fh.read(), re.M)
            if m:
                data["port"] = int(m.group(1))
                migrated.append("port")
        except OSError:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # folders.json (extra shared folders)
    old_folders = os.path.join(old_dir, "folders.json")
    new_folders = os.path.join(os.path.dirname(path), "folders.json")
    if os.path.isfile(old_folders) and not os.path.isfile(new_folders):
        shutil.copy2(old_folders, new_folders)
        migrated.append("folders.json")
    # TLS cert + key, so phones keep their pinned certificate.
    old_certs = os.path.join(old_dir, "certs")
    cert_dir = data.get("cert_dir") or p.get("cert_dir")
    if cert_dir and os.path.isfile(os.path.join(old_certs, "server-cert.pem")) \
            and os.path.isfile(os.path.join(old_certs, "server-key.pem")) \
            and not os.path.isfile(os.path.join(cert_dir, "server-cert.pem")):
        os.makedirs(cert_dir, exist_ok=True)
        for f in ("server-cert.pem", "server-key.pem"):
            shutil.copy2(os.path.join(old_certs, f), os.path.join(cert_dir, f))
        migrated.append("certs")
    if p.get("cert_dir") and "cert_dir" not in data:
        data["cert_dir"] = p["cert_dir"]
    cfg.write_json_atomic(path, data)
    return _out({"ok": True, "migrated": migrated, "old_dir": old_dir,
                 "code_ok": cfg.validate_access_code(str(data.get("access_code") or "")) is None,
                 "folder_ok": _folder_report(data.get("media_folder", ""))["ok"]
                 if data.get("media_folder") else False})


def cmd_show_code(_p):
    cfg = _config()
    return _out({"ok": bool(cfg.ACCESS_CODE), "code": cfg.ACCESS_CODE})


def cmd_info(_p):
    cfg = _config()
    import server  # venv only (Flask etc.)
    ips = server.get_lan_ips()
    roots = server.get_roots()
    return _out({
        "ok": True,
        "lan_ips": ips,
        "port": cfg.PORT, "https_port": cfg.HTTPS_PORT,
        "fingerprint": server.cert_fingerprint(),
        "log_file": os.path.join(cfg.LOG_DIR, "server.log"),
        "config_file": cfg.CONFIG_FILE,
        "roots": [{"name": n, "path": p, "accessible": os.path.isdir(p)} for n, p in roots.items()],
        "auto_organize": cfg.AUTO_ORGANIZE, "organize_existing": cfg.ORGANIZE_EXISTING,
    })


def cmd_selftest(_p):
    """Import everything the server needs (run inside the venv)."""
    import flask, werkzeug, PIL, jwt, dotenv  # noqa: F401
    from PIL import Image, ImageOps  # noqa: F401  (fails if the C extension is broken)
    try:
        import cryptography  # noqa: F401
        https = True
    except ImportError:
        https = False
    import server
    return _out({"ok": True, "https": https, "heif": server.HEIF_AVAILABLE,
                 "ffmpeg": bool(shutil.which("ffmpeg")), "pillow": PIL.__version__})


def cmd_add_folder(p):
    import add_folder
    ok, msg = add_folder.add_folder(str(p.get("name") or ""), str(p.get("path") or ""))
    return _out({"ok": ok, "reason": "" if ok else msg, "message": msg})


def cmd_list_folders(_p):
    import add_folder
    return _out({"ok": True, "folders": add_folder.load_folders()})


COMMANDS = {
    "env-info": cmd_env_info, "gen-code": cmd_gen_code, "validate-code": cmd_validate_code,
    "validate-folder": cmd_validate_folder, "read-config": cmd_read_config,
    "write-config": cmd_write_config, "migrate-env": cmd_migrate_env,
    "show-code": cmd_show_code, "info": cmd_info, "add-folder": cmd_add_folder,
    "list-folders": cmd_list_folders, "selftest": cmd_selftest,
}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        sys.stderr.write(__doc__ + "\n")
        return 2
    try:
        payload = {} if argv[0] in ("env-info", "gen-code", "selftest") else _payload()
        return COMMANDS[argv[0]](payload)
    except Exception as e:   # always answer in the protocol
        return _out({"ok": False, "reason": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    raise SystemExit(main())

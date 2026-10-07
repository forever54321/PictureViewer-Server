"""Lumina Gallery Server configuration.

Where settings come from (highest priority first):

1. ``config.json`` — written by the installers as JSON (no quoting/escaping
   problems with special characters). Location: the file named by the
   ``PICTUREVIEWER_CONFIG`` environment variable, else ``config.json`` next
   to this file.
2. Process environment variables (``PICTUREVIEWER_*``).
3. ``.env`` next to this file — what older installers wrote. Still read so
   existing installs keep working after updating. It is parsed WITHOUT
   ``${VAR}`` interpolation. NOTE: in an unquoted .env value, `` #`` (a space
   followed by #) starts a comment — wrap such values in single quotes,
   e.g. ``PICTUREVIEWER_ACCESS_CODE='My code #1 Rocks!'``. A ``#`` with no
   space before it (``Sunset#Beach2026``) is kept as-is.

Importing this module has no side effects beyond reading those files: the
access-code policy is enforced by the server at startup (see
``enforce_startup_policy``), and a missing SECRET_KEY is generated and
persisted lazily by ``ensure_secret_key``.
"""
from __future__ import annotations

import io
import os
import json
import secrets
import tempfile
from pathlib import Path

APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.environ.get("PICTUREVIEWER_CONFIG") or os.path.join(APP_DIR, "config.json")
CONFIG_FILE = os.path.abspath(os.path.expanduser(CONFIG_FILE))
CONFIG_DIR = os.path.dirname(CONFIG_FILE)
ENV_FILE = os.path.join(APP_DIR, ".env")
SECRET_KEY_FILE = os.path.join(APP_DIR, "secret.key")

try:
    with open(os.path.join(APP_DIR, "VERSION"), encoding="utf-8") as _fh:
        SERVER_VERSION = _fh.read().strip() or "dev"
except OSError:
    SERVER_VERSION = "dev"


def _read_json_config(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)        # a corrupt config.json is a hard error, not silently ignored
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return data


def read_env_file(path: str) -> dict:
    """Parse a .env file (tolerates a UTF-8 BOM, which older PowerShell
    installers wrote) without interpolation. Returns {} if missing."""
    if not os.path.isfile(path):
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    vals = dotenv_values(stream=io.StringIO(text), interpolate=False)
    return {k: v for k, v in vals.items() if k and v is not None}


_JSON = _read_json_config(CONFIG_FILE)
USING_CONFIG_JSON = os.path.isfile(CONFIG_FILE)

# .env is loaded into the process environment WITHOUT overriding variables that
# are already set (same behaviour as the old load_dotenv call).
for _k, _v in read_env_file(ENV_FILE).items():
    os.environ.setdefault(_k, _v)


def _setting(json_key: str, env_key: str | None, default):
    if json_key in _JSON and _JSON[json_key] is not None:
        return _JSON[json_key]
    if env_key and os.environ.get(env_key) not in (None, ""):
        return os.environ[env_key]
    return default


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# Server Configuration
HOST = str(_setting("host", "PICTUREVIEWER_HOST", "0.0.0.0"))
PORT = _as_int(_setting("port", "PICTUREVIEWER_PORT", 8500), 8500)               # plain HTTP
HTTPS_PORT = _as_int(_setting("https_port", "PICTUREVIEWER_HTTPS_PORT", 8543), 8543)  # TLS, pinned by the app

# Media folder (the main shared folder, shown in the app as "Library")
MEDIA_FOLDER = str(_setting("media_folder", "PICTUREVIEWER_MEDIA_FOLDER", str(Path.home() / "Pictures")))

# Extra named shared folders {name: path}. Usually managed in folders.json
# (see FOLDERS_FILE / add_folder.py); config.json may also carry them.
MEDIA_ROOTS: dict = dict(_JSON["media_roots"]) if isinstance(_JSON.get("media_roots"), dict) else {}

# folders.json (extra roots added with add_folder) lives next to the config
# (for existing installs that is next to this file, exactly as before).
FOLDERS_FILE = os.path.abspath(str(_setting("folders_file", "PICTUREVIEWER_FOLDERS_FILE",
                                            os.path.join(CONFIG_DIR, "folders.json"))))

# Thumbnail cache / certificates / logs
THUMBNAIL_FOLDER = os.path.abspath(str(_setting("thumbnail_dir", "PICTUREVIEWER_THUMBNAIL_DIR",
                                                os.path.join(APP_DIR, ".thumbnails"))))
# None = legacy location (a "certs" folder next to the thumbnail cache).
CERT_DIR = _setting("cert_dir", "PICTUREVIEWER_CERT_DIR", None)
LOG_DIR = os.path.abspath(str(_setting("log_dir", "PICTUREVIEWER_LOG_DIR", os.path.join(APP_DIR, "logs"))))
THUMBNAIL_CACHE_MAX_MB = _as_int(_setting("thumbnail_cache_max_mb", None, 2048), 2048)

# Supported file extensions (anything else is never listed, served or organized)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif",
                    ".tiff", ".tif", ".dng"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm",
                    ".3gp", ".mts", ".m2ts"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Authentication
SECRET_KEY = str(_setting("secret_key", "PICTUREVIEWER_SECRET_KEY", "") or "")
ACCESS_CODE = str(_setting("access_code", "PICTUREVIEWER_ACCESS_CODE", "") or "")
TOKEN_EXPIRY_HOURS = 720  # 30 days

# Upload settings
MAX_UPLOAD_SIZE_MB = _as_int(_setting("max_upload_size_mb", "PICTUREVIEWER_MAX_UPLOAD_MB", 10240), 10240)
MIN_FREE_SPACE_MB = _as_int(_setting("min_free_space_mb", None, 1024), 1024)

# Automatic organization. When True, uploaded files are sorted into
#     Photos/<Year>/<MM-Month>/    e.g. Photos/2026/01-January/
#     Videos/<Year>/<MM-Month>/
# by capture date (EXIF / video metadata, else filename date, else the folder
# the file is already in, else modified time). Original filenames are kept.
AUTO_ORGANIZE = _as_bool(_setting("auto_organize", "PICTUREVIEWER_AUTO_ORGANIZE", "1"))
# When True (and AUTO_ORGANIZE is on), files ALREADY in the shared folders are
# also sorted at startup (moved, never deleted or overwritten). Default True to
# keep the behaviour existing installs rely on; the installer asks.
ORGANIZE_EXISTING = _as_bool(_setting("organize_existing", "PICTUREVIEWER_ORGANIZE_EXISTING", "1"))

# Extra Host header values accepted by the DNS-rebinding guard (IP literals,
# localhost and *.local are always accepted). Exact names, or ".example.ts.net"
# to accept a whole suffix — e.g. a Tailscale MagicDNS name.
_hosts = _setting("allowed_hosts", "PICTUREVIEWER_ALLOWED_HOSTS", [])
if isinstance(_hosts, str):
    _hosts = [h for h in (x.strip() for x in _hosts.split(",")) if h]
ALLOWED_HOSTS = [str(h).strip().lower() for h in (_hosts or []) if str(h).strip()]


def validate_access_code(code: str):
    """Enforce the access-code policy. Returns None if OK, else a short reason.

    Policy: at least 12 characters AND at least one lowercase letter, one
    uppercase letter, one number, and one special (non-alphanumeric) character.
    """
    code = code or ""
    if len(code) < 12:
        return "at least 12 characters long"
    if not any(c.islower() for c in code):
        return "at least one lowercase letter"
    if not any(c.isupper() for c in code):
        return "at least one uppercase letter"
    if not any(c.isdigit() for c in code):
        return "at least one number"
    if not any((not c.isalnum()) and (not c.isspace()) for c in code):
        return "at least one special character (e.g. ! ? # $ %)"
    if any(ord(c) < 32 or ord(c) == 127 for c in code):
        return "no control characters"
    return None


def enforce_startup_policy():
    """Raise RuntimeError if the server must not start with this access code."""
    problem = validate_access_code(ACCESS_CODE)
    if problem:
        raise RuntimeError(
            "Refusing to start: the access code does not meet the security policy "
            f"(it must contain {problem}). Choose a code of at least 12 "
            "characters with lowercase, uppercase, a number, and a special "
            "character, then run setup again (or set access_code in config.json / "
            "PICTUREVIEWER_ACCESS_CODE in .env)."
        )


def write_json_atomic(path: str, data: dict):
    """Write JSON via a temp file + os.replace so a crash never leaves a
    half-written config. The file is created owner-only (0600 on POSIX; on
    Windows the installer locks the folder down with icacls)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cfg_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_secret_key() -> str:
    """Make sure SECRET_KEY is strong (>= 32 chars). If it isn't, generate one
    and PERSIST it — into config.json when that is in use, else into
    ``secret.key`` next to this file (owner-only permissions) — so a restart
    doesn't log every phone out. Never returns an empty key."""
    global SECRET_KEY
    if len(SECRET_KEY or "") >= 32:
        return SECRET_KEY
    new_key = secrets.token_hex(32)
    if os.path.isfile(CONFIG_FILE):
        data = _read_json_config(CONFIG_FILE)
        existing = str(data.get("secret_key") or "")
        if len(existing) >= 32:
            SECRET_KEY = existing
            return SECRET_KEY
        data["secret_key"] = new_key
        write_json_atomic(CONFIG_FILE, data)
        SECRET_KEY = new_key
        return SECRET_KEY
    # Legacy (.env) installs: a separate secret.key file next to this file.
    try:
        with open(SECRET_KEY_FILE, encoding="utf-8") as fh:
            existing = fh.read().strip()
        if len(existing) >= 32:
            SECRET_KEY = existing
            return SECRET_KEY
    except OSError:
        pass
    try:
        fd = os.open(SECRET_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_key)
        SECRET_KEY = new_key
    except FileExistsError:
        # Another process won the race (or an old short key is there): reuse
        # it if usable, otherwise replace it atomically.
        try:
            with open(SECRET_KEY_FILE, encoding="utf-8") as fh:
                existing = fh.read().strip()
        except OSError:
            existing = ""
        if len(existing) >= 32:
            SECRET_KEY = existing
        else:
            tmp = SECRET_KEY_FILE + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_key)
            os.replace(tmp, SECRET_KEY_FILE)
            SECRET_KEY = new_key
    return SECRET_KEY


def app_dirs() -> list:
    """Directories a shared folder must never contain or be inside."""
    return [APP_DIR, CONFIG_DIR]

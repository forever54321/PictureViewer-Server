#!/usr/bin/env python3
"""
Lumina Gallery Server - Secure media file server for the Lumina Gallery iOS app.
Run this on your Mac or PC to share a folder of photos and videos.
"""
from __future__ import annotations

import os
import sys
import ssl
import json
import time
import hmac
import socket
import hashlib
import datetime
import calendar
import ipaddress
import mimetypes
import threading
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, send_file, abort
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass
import jwt

import config

import shutil
import tempfile

app = Flask(__name__)
# Set very high limits for large video uploads
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024 * 1024  # 16GB

# Fix Werkzeug form parsing limits for large uploads
try:
    # Werkzeug 3.x
    from werkzeug.formparser import MultiPartParser
    MultiPartParser.max_form_memory_size = 16 * 1024 * 1024 * 1024
except (ImportError, AttributeError):
    pass

try:
    # Also try setting on the request class
    from werkzeug.wrappers import Request as WerkzeugRequest
    WerkzeugRequest.max_content_length = 16 * 1024 * 1024 * 1024
    WerkzeugRequest.max_form_memory_size = 16 * 1024 * 1024 * 1024
    WerkzeugRequest.max_form_parts = 10000
except (ImportError, AttributeError):
    pass

# No CORS headers are emitted. The native iOS client does not need CORS;
# omitting it stops browsers from reading this server's responses cross-origin.


@app.before_request
def _block_dns_rebinding():
    """Reject requests whose Host header is a domain name.

    A DNS-rebinding attack works by getting the victim's browser to load a
    page from attacker.com, then re-pointing attacker.com at this server's
    LAN IP so the page's JavaScript can talk to it. In that attack the
    browser still sends `Host: attacker.com`. Legitimate clients reach this
    server by IP (http://192.168.x.x:8500), so we only accept IP-literal,
    localhost, or *.local (Bonjour) hosts.
    """
    host = (request.host or "").split(":")[0].strip("[]")
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        abort(403, description="Invalid Host header")


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File too large for server", "success": False}), 413

# Ensure directories exist
os.makedirs(config.THUMBNAIL_FOLDER, exist_ok=True)

# Validate that at least one configured folder is accessible. With multi-root
# setups, individual missing roots are reported via /api/status (so the iOS
# app can flag them) — we only refuse to start if EVERY root is missing.
_configured_roots = getattr(config, "MEDIA_ROOTS", None)
if isinstance(_configured_roots, dict) and _configured_roots:
    _accessible = [n for n, p in _configured_roots.items() if os.path.isdir(p)]
    if not _accessible:
        sys.stderr.write(
            "\nERROR: None of the configured media folders are accessible:\n"
            + "\n".join(f"  {n}: {p}" for n, p in _configured_roots.items())
            + "\n\nMount the drive(s) or run setup again.\n"
        )
        sys.exit(2)
elif not os.path.isdir(config.MEDIA_FOLDER):
    sys.stderr.write(
        f"\nERROR: Media folder is not accessible:\n  {config.MEDIA_FOLDER}\n\n"
        "Possible causes:\n"
        "  - The drive containing the folder is not mounted\n"
        "  - The folder was renamed, moved, or deleted\n"
        "  - PICTUREVIEWER_MEDIA_FOLDER environment variable is wrong\n\n"
        "Restore the folder, mount the drive, or run setup again to choose "
        "a new folder.\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def create_token():
    payload = {
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=config.TOKEN_EXPIRY_HOURS),
        "iat": datetime.datetime.now(datetime.timezone.utc),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            return jsonify({"error": "Token required"}), 401
        try:
            jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


def _extra_roots_file() -> str:
    """folders.json lives next to config.py — written by the add_folder tool."""
    return os.path.join(os.path.dirname(os.path.abspath(config.__file__)), "folders.json")


def get_roots() -> dict:
    """All shared folders ("roots") as {name: path}, in the order:
    the main media folder, then any extra folders the user added (via the
    add_folder tool's folders.json, or the GUI launcher's MEDIA_ROOTS).

    Read live each call, so a folder added with the tool shows up in the app
    without restarting the server. Both phones use the same access code and
    pick which of these folders to back up to.
    """
    roots: dict = {}
    main = config.MEDIA_FOLDER
    if main:
        roots["Library"] = main          # keep the legacy name for the main folder

    cfg_roots = getattr(config, "MEDIA_ROOTS", None)
    if isinstance(cfg_roots, dict):
        for n, p in cfg_roots.items():
            if n and p:
                roots[str(n)] = str(p)

    try:
        with open(_extra_roots_file(), encoding="utf-8") as fh:
            extra = json.load(fh)
        if isinstance(extra, dict):
            for n, p in extra.items():
                if n and p:
                    roots[str(n)] = str(p)
    except Exception:
        pass

    return roots or {"Library": config.MEDIA_FOLDER}


def get_root_path(name: str) -> str | None:
    roots = get_roots()
    if name and name in roots:
        return roots[name]
    if name:
        return None
    # No name supplied — default to first root.
    return next(iter(roots.values()), config.MEDIA_FOLDER)


# Magic-byte signatures keyed by file extension. The check is "first N bytes
# of file START WITH any of these" — so this only accepts genuine image/video
# headers. Extensions in IMAGE_EXTENSIONS / VIDEO_EXTENSIONS without an entry
# here are accepted with extension-only validation (caller's risk to expand).
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    ".jpg":  (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png":  (b"\x89PNG\r\n\x1a\n",),
    ".gif":  (b"GIF87a", b"GIF89a"),
    ".bmp":  (b"BM",),
    ".webp": (b"RIFF",),  # followed by 4-byte size, then "WEBP"
    ".heic": (b"\x00\x00\x00",),  # ftypheic at offset 4 — see deeper check below
    ".heif": (b"\x00\x00\x00",),
    ".tif":  (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
    ".mp4":  (b"\x00\x00\x00",),  # ftypmp4 at offset 4
    ".m4v":  (b"\x00\x00\x00",),
    ".mov":  (b"\x00\x00\x00",),  # ftypqt at offset 4
    ".webm": (b"\x1aE\xdf\xa3",),
    ".mkv":  (b"\x1aE\xdf\xa3",),
    ".avi":  (b"RIFF",),  # followed by 4-byte size, then "AVI "
    ".wmv":  (b"\x30\x26\xb2\x75",),
}


def _content_matches_extension(path: Path, ext: str) -> bool:
    sigs = _MAGIC_BYTES.get(ext.lower())
    if not sigs:
        # No signature on file for this extension — accept (caller used the
        # extension allowlist; this function is the second line of defense
        # only for types we have a signature for).
        return True
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    return any(head.startswith(s) for s in sigs)


def _is_within(target: Path, base: Path) -> bool:
    """True only if `target` is `base` itself or a descendant of it.

    Uses Path.relative_to rather than str.startswith — the latter is unsafe
    because "/media/photos" startswith-matches "/media/photos-secret".
    """
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def safe_path(relative: str, root_name: str = "") -> Path:
    """Resolve a relative path inside the chosen root and reject traversal."""
    root = get_root_path(root_name)
    if root is None:
        abort(404, description=f"Unknown folder: {root_name}")
    base = Path(root).resolve()
    # Reject absolute paths outright; they would escape the join entirely.
    if os.path.isabs(relative):
        abort(403, description="Absolute paths are not allowed")
    target = (base / relative).resolve()
    if not _is_within(target, base):
        abort(403, description="Path is outside the shared folder")
    return target


def relative_to_root(target: Path, root_name: str) -> str:
    root = get_root_path(root_name) or config.MEDIA_FOLDER
    return str(target.relative_to(Path(root).resolve()))


# ---------------------------------------------------------------------------
# Automatic organization — sort uploads (and any loose existing media) into
#   <root>/Photos/<Year>/<MM-Month>/         for photos  (e.g. 2026/01-January)
#   <root>/Videos/<Year>/<MonthName>/        for videos
# The folder components are derived ONLY from the file's capture date and a
# fixed media-type label, never from client input, so there is no path-
# traversal surface here. Filenames are still run through secure_filename.
# ---------------------------------------------------------------------------

import re as _re

_EXTRA_VIDEO_EXTS = {".3gp", ".flv", ".ts", ".mts", ".m2ts"}
_TOP_FOLDERS = ("Photos", "Videos")
_MONTH_NAMES = {
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}
_QT_EPOCH = datetime.datetime(1904, 1, 1)
# Matches 8-digit dates anywhere in a name: 2026-06-29, 20260629, VID_20260629_…
_FILENAME_DATE_RE = _re.compile(r"(20\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])")


# Directory packages we must NEVER walk into or move files out of — doing so
# would corrupt the app's library. The biggest one is the macOS Photos Library
# (.photoslibrary), which lives in the default ~/Pictures folder.
_PACKAGE_DIR_SUFFIXES = (
    ".photoslibrary", ".photolibrary", ".aplibrary", ".migratedaplibrary",
    ".lrlibrary", ".lrdata", ".imovielibrary", ".tvlibrary", ".theater",
    ".fcpbundle", ".musiclibrary", ".app", ".bundle", ".framework",
    ".photoslibrary", ".pkpass", ".rcproject",
)


def _is_package_dir(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(s) for s in _PACKAGE_DIR_SUFFIXES)


def _is_video_ext(ext: str) -> bool:
    ext = ext.lower()
    return ext in config.VIDEO_EXTENSIONS or ext in _EXTRA_VIDEO_EXTS


def _is_image_ext(ext: str) -> bool:
    return ext.lower() in config.IMAGE_EXTENSIONS


def _sane_date(d):
    """Reject absurd dates so a corrupt EXIF/atom can't create folders like 5921/."""
    if d is None:
        return None
    if d.year < 1990 or d.year > 2100:
        return None
    return d


def _parse_exif_dt(val):
    try:
        s = str(val).strip()
        return datetime.datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def _exif_date(path: Path):
    """DateTimeOriginal (or DateTime) from a photo's EXIF, if present."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            for tag in (36867, 306):  # DateTimeOriginal, DateTime
                d = _parse_exif_dt(exif.get(tag)) if exif.get(tag) else None
                if d:
                    return d
            try:
                sub = exif.get_ifd(0x8769)  # Exif sub-IFD
                for tag in (0x9003, 0x9004):  # DateTimeOriginal, DateTimeDigitized
                    d = _parse_exif_dt(sub.get(tag)) if sub.get(tag) else None
                    if d:
                        return d
            except Exception:
                pass
    except Exception:
        return None
    return None


def _find_atom(f, wanted: bytes, start: int, end: int):
    """Find an MP4/QuickTime box `wanted` between byte offsets [start, end)."""
    pos = start
    for _ in range(100000):  # hard cap — never loop forever on a malformed file
        if pos + 8 > end:
            return None
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        size = int.from_bytes(hdr[0:4], "big")
        atom = hdr[4:8]
        header_len = 8
        if size == 1:  # 64-bit extended size
            ext = f.read(8)
            if len(ext) < 8:
                return None
            size = int.from_bytes(ext, "big")
            header_len = 16
        elif size == 0:  # extends to end of parent
            size = end - pos
        if size < header_len or pos + size > end:
            return None
        if atom == wanted:
            return (pos, size, header_len)
        pos += size
    return None


def _mvhd_date(path: Path):
    """Creation time from an MP4/MOV moov→mvhd box (QuickTime 1904 epoch)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            fsize = f.tell()
            moov = _find_atom(f, b"moov", 0, fsize)
            if not moov:
                return None
            m_start, m_size, m_hdr = moov
            mvhd = _find_atom(f, b"mvhd", m_start + m_hdr, m_start + m_size)
            if not mvhd:
                return None
            v_start, v_size, v_hdr = mvhd
            f.seek(v_start + v_hdr)
            body = f.read(min(v_size - v_hdr, 24))
            if len(body) < 8:
                return None
            version = body[0]
            if version == 1:
                if len(body) < 12:
                    return None
                secs = int.from_bytes(body[4:12], "big")
            else:
                secs = int.from_bytes(body[4:8], "big")
            if secs <= 0:
                return None
            return _sane_date(_QT_EPOCH + datetime.timedelta(seconds=secs))
    except Exception:
        return None


def _filename_date(name: str):
    m = _FILENAME_DATE_RE.search(name or "")
    if not m:
        return None
    try:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


# Month-name / number lookup for reading the user's EXISTING year/month folders.
_MONTH_TOKEN = {}
for _i in range(1, 13):
    _MONTH_TOKEN[calendar.month_name[_i].lower()] = _i   # "june"
    _MONTH_TOKEN[calendar.month_abbr[_i].lower()] = _i   # "jun"


def _month_from_token(tok: str):
    t = tok.strip().lower()
    if t in _MONTH_TOKEN:                       # "June", "Jun"
        return _MONTH_TOKEN[t]
    m = _re.match(r"(\d{1,2})(?:\D|$)", t)      # "06", "6", "06-June"
    if m:
        v = int(m.group(1))
        if 1 <= v <= 12:
            return v
    for name, idx in _MONTH_TOKEN.items():      # "June 2024", "2024-June"
        if len(name) > 3 and name in t:
            return idx
    return None


def _folder_date(path: Path):
    """Infer a date from the folder names a file already sits in, so existing
    Year/Month organization is preserved for files that have no embedded date."""
    year = month = None
    for parent in list(path.parents)[:4]:
        part = parent.name
        if year is None:
            ym = _re.search(r"(20\d{2})", part)
            if ym:
                year = int(ym.group(1))
        if month is None:
            month = _month_from_token(part)
        if year and month:
            break
    if year and month:
        try:
            return datetime.datetime(year, month, 1)
        except Exception:
            return None
    return None


def _capture_date(path: Path, ext: str, hint_name: str = ""):
    """Best-effort capture date: EXIF → name → video metadata → existing
    Year/Month folder → file mtime."""
    ext = ext.lower()
    d = None
    if _is_image_ext(ext):
        d = _sane_date(_exif_date(path))
    if d is None:
        d = _sane_date(_filename_date(hint_name or path.name))
    if d is None and _is_video_ext(ext):
        d = _mvhd_date(path)
    if d is None:
        d = _sane_date(_folder_date(path))
    if d is None:
        try:
            d = datetime.datetime.fromtimestamp(path.stat().st_mtime)
        except Exception:
            d = datetime.datetime.now()
    return d


def _organized_subpath(date, is_video: bool) -> Path:
    top = "Videos" if is_video else "Photos"
    month = "%02d-%s" % (date.month, date.strftime("%B"))   # e.g. "01-January"
    return Path(top) / str(date.year) / month


def _unique_dest(folder: Path, safe_name: str) -> Path:
    ext = Path(safe_name).suffix
    stem = Path(safe_name).stem
    dest = folder / safe_name
    counter = 1
    while dest.exists():
        dest = folder / f"{stem}_{counter}{ext}"
        counter += 1
    return dest


def _move_into_place(src: Path, base: Path) -> Path | None:
    """Move one already-validated media file into its organized folder under
    `base`. Returns the new path, or None if it was left where it is."""
    ext = src.suffix.lower()
    is_vid = _is_video_ext(ext)
    if not (is_vid or _is_image_ext(ext)):
        return None
    date = _capture_date(src, ext, hint_name=src.name)
    dest_folder = (base / _organized_subpath(date, is_vid)).resolve()
    # Belt-and-suspenders: the computed folder must stay inside the root.
    if not _is_within(dest_folder, base):
        return None
    dest_folder.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(src.name) or src.name
    dest = _unique_dest(dest_folder, safe_name)
    if dest.resolve() == src.resolve():
        return None
    try:
        os.replace(str(src), str(dest))        # atomic within the same filesystem
    except OSError:
        shutil.move(str(src), str(dest))       # fallback across devices
    return dest


def _is_already_organized(src: Path, base: Path) -> bool:
    try:
        rel = src.resolve().relative_to(base)
    except ValueError:
        return False
    parts = rel.parts
    if len(parts) != 4:
        return False
    return (parts[0] in _TOP_FOLDERS
            and bool(_re.fullmatch(r"20\d{2}", parts[1]))
            and bool(_re.fullmatch(r"(0[1-9]|1[0-2])-[A-Za-z]+", parts[2])))


def organize_existing(base_path: str):
    """Sort any loose/un-organized media already in a root into the structure.
    Idempotent: files already in <Pictures|Videos>/<Year>/<Month>/ are skipped.
    Never deletes; only moves, with collision-safe renaming."""
    base = Path(base_path).resolve()
    if not base.is_dir():
        return (0, 0)
    moved = skipped = seen = 0
    for dirpath, dirnames, filenames in os.walk(base):
        # Don't descend into hidden/system dirs (.thumbnails, .incoming, certs…)
        # OR into app library packages (.photoslibrary etc.) — walking into a
        # Photos Library and moving its internal files out would corrupt it.
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d != "certs" and not _is_package_dir(d)
        ]
        cur = Path(dirpath)
        for fn in filenames:
            if fn.startswith("."):
                continue
            src = cur / fn
            if src.is_symlink():               # never follow links out of the root
                continue
            ext = src.suffix.lower()
            if not (_is_video_ext(ext) or _is_image_ext(ext)):
                continue
            seen += 1
            # Heartbeat so a large library doesn't look frozen at startup.
            if seen % 200 == 0:
                print(f"    …{seen} files checked, {moved} organized so far", flush=True)
            if _is_already_organized(src, base):
                skipped += 1
                continue
            try:
                if _move_into_place(src, base):
                    moved += 1
            except Exception:
                # One bad file must never abort the whole pass.
                continue

    # Tidy up folders left empty by the moves (e.g. an old Year/Month tree whose
    # files were re-homed). os.rmdir only removes EMPTY directories, so this can
    # never delete a file or a non-empty folder. Deepest-first; never touches the
    # root, hidden dirs, or library packages.
    if moved:
        candidates = []
        for dpath, dnames, _fn in os.walk(base, topdown=True):
            dnames[:] = [d for d in dnames
                         if not d.startswith(".") and d != "certs" and not _is_package_dir(d)]
            if Path(dpath).resolve() != base:
                candidates.append(dpath)
        for dpath in sorted(candidates, key=len, reverse=True):
            try:
                os.rmdir(dpath)        # succeeds only if the directory is empty
            except OSError:
                pass
    return (moved, skipped)


def organize_all_roots():
    if not getattr(config, "AUTO_ORGANIZE", True):
        return
    print("  Organizing your library into Photos/Videos by year and month…")
    print("  (the first run can take a few minutes for large folders)")
    for name, path in get_roots().items():
        try:
            moved, skipped = organize_existing(path)
            print(f"  '{name}': {moved} file(s) organized, {skipped} already in place.")
        except Exception as e:
            sys.stderr.write(f"  Could not organize root '{name}': {e}\n")
    print("  Organize pass complete.\n", flush=True)


# ---------------------------------------------------------------------------
# TLS — self-signed certificate, pinned by the iOS app via SHA-256 fingerprint
# ---------------------------------------------------------------------------

# Populated by start_servers() so /api/status can report the live ports.
_runtime = {"http_port": None, "https_port": None}


def _cert_paths():
    """certs live next to the thumbnail cache, in a per-user writable dir."""
    cert_dir = os.path.join(
        os.path.dirname(os.path.abspath(config.THUMBNAIL_FOLDER)), "certs"
    )
    return (
        cert_dir,
        os.path.join(cert_dir, "server-cert.pem"),
        os.path.join(cert_dir, "server-key.pem"),
    )


def ensure_certificate():
    """Generate a self-signed cert covering the local IPs / hostnames if one
    doesn't exist yet. Returns (cert_path, key_path), or (None, None) if the
    cryptography library isn't installed (server then runs HTTP-only)."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        sys.stderr.write(
            "Note: 'cryptography' not installed — running HTTP only. "
            "Run the installer again to enable HTTPS.\n"
        )
        return None, None

    cert_dir, cert_path, key_path = _cert_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    os.makedirs(cert_dir, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Subject Alternative Names — every address a client might connect to.
    san = [x509.DNSName("localhost")]
    hostname = socket.gethostname()
    if hostname:
        san.append(x509.DNSName(hostname))
        if not hostname.endswith(".local"):
            san.append(x509.DNSName(hostname + ".local"))
    seen_ips = set()
    for ip in list(get_local_ips()) + ["127.0.0.1"]:
        if ip in seen_ips:
            continue
        seen_ips.add(ip)
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Lumina Gallery Server")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    try:
        os.chmod(key_path, 0o600)  # private key — owner-readable only
    except OSError:
        pass
    return cert_path, key_path


def cert_fingerprint() -> str:
    """Lowercase hex SHA-256 fingerprint of the server certificate.

    Not a secret — it's a public-key hash. The iOS app pins this so it will
    only ever trust *this* server's self-signed cert. Empty if no cert."""
    _, cert_path, _ = _cert_paths()
    if not os.path.isfile(cert_path):
        return ""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        return cert.fingerprint(hashes.SHA256()).hex()
    except Exception:
        return ""


def start_servers(http_port: int = None, https_port: int = None):
    """Start the HTTP server, and the HTTPS server too if a cert is available.

    Both run in background daemon threads; this function returns immediately.
    The caller is responsible for keeping the process alive."""
    from werkzeug.serving import make_server

    # Organize any loose/existing media into Pictures|Videos/<Year>/<Month>/
    # BEFORE we start accepting connections, so the library is already tidy by
    # the time the first upload arrives. Wrapped so it can never block startup.
    try:
        organize_all_roots()
    except Exception as e:
        sys.stderr.write(f"  Initial organize pass skipped: {e}\n")

    http_port = http_port or config.PORT
    https_port = https_port or getattr(config, "HTTPS_PORT", 8543)

    # HTTP — always on (reachability checks + transition for old app versions).
    http_srv = make_server(config.HOST, http_port, app, threaded=True)
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    _runtime["http_port"] = http_port

    # HTTPS — best effort. A failure here must not take the server down.
    cert_path, key_path = ensure_certificate()
    if cert_path and key_path:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_path, key_path)
            https_srv = make_server(
                config.HOST, https_port, app, threaded=True, ssl_context=ctx
            )
            threading.Thread(target=https_srv.serve_forever, daemon=True).start()
            _runtime["https_port"] = https_port
        except Exception as e:
            sys.stderr.write(f"HTTPS could not start ({e}) — continuing HTTP-only.\n")

    return dict(_runtime)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# In-memory brute-force throttle for /api/auth. Keyed by client IP.
_AUTH_MAX_FAILURES = 5      # failures allowed within the window
_AUTH_WINDOW = 300         # seconds — rolling window for counting failures
_auth_failures: dict[str, list[float]] = {}


def _recent_failures(ip: str, now: float) -> list[float]:
    return [t for t in _auth_failures.get(ip, []) if now - t < _AUTH_WINDOW]


@app.route("/api/auth", methods=["POST"])
def authenticate():
    ip = request.remote_addr or "unknown"
    now = time.time()

    fails = _recent_failures(ip, now)
    if len(fails) >= _AUTH_MAX_FAILURES:
        retry_in = int(_AUTH_WINDOW - (now - fails[0]))
        return jsonify({
            "error": f"Too many failed attempts. Try again in {retry_in}s."
        }), 429

    data = request.get_json(silent=True) or {}
    code = data.get("code", "")

    # Constant-time comparison defeats timing attacks on the access code.
    if hmac.compare_digest(str(code), str(config.ACCESS_CODE)):
        _auth_failures.pop(ip, None)
        return jsonify({"token": create_token()})

    fails.append(now)
    _auth_failures[ip] = fails
    # Opportunistically drop stale IP entries so the dict can't grow forever.
    if len(_auth_failures) > 1000:
        for k in [k for k, v in _auth_failures.items()
                  if not _recent_failures(k, now)]:
            _auth_failures.pop(k, None)
    return jsonify({"error": "Invalid access code"}), 401


def _has_valid_token() -> bool:
    """True if a valid Bearer token is present — without requiring one."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    try:
        jwt.decode(auth_header[7:], config.SECRET_KEY, algorithms=["HS256"])
        return True
    except jwt.InvalidTokenError:
        return False


@app.route("/api/status", methods=["GET"])
def status():
    folder = config.MEDIA_FOLDER
    folder_name = os.path.basename(folder.rstrip(os.sep)) or folder
    # Absolute filesystem paths reveal the OS username and directory layout,
    # so only expose them to authenticated clients. Unauthenticated callers
    # (reachability checks, port scanners) get folder names only.
    authed = _has_valid_token()
    return jsonify({
        "status": "ok",
        "name": "Lumina Gallery Server",
        "media_folder": folder if authed else folder_name,
        "media_folder_name": folder_name,
        "media_folder_accessible": os.path.isdir(folder),
        # HTTPS discovery — the app upgrades to TLS and pins this fingerprint.
        "https_port": _runtime.get("https_port"),
        "https_available": _runtime.get("https_port") is not None,
        "tls_fingerprint": cert_fingerprint(),
        # Capability flag: this server accepts streamed raw-body uploads
        # (X-Upload-* headers). The app uses them to avoid writing a second
        # on-disk copy of large videos; older servers omit this and the app
        # falls back to multipart.
        "raw_upload": True,
        "roots": [
            {
                "name": n,
                "path": (p if authed else os.path.basename(p.rstrip(os.sep)) or n),
                "accessible": os.path.isdir(p),
            }
            for n, p in get_roots().items()
        ],
    })


@app.route("/api/roots", methods=["GET"])
@token_required
def list_roots():
    return jsonify({
        "roots": [
            {"name": n, "path": p, "accessible": os.path.isdir(p)}
            for n, p in get_roots().items()
        ]
    })


@app.route("/api/files", methods=["GET"])
@token_required
def list_files():
    root_name = request.args.get("root", "")
    subfolder = request.args.get("path", "")
    folder = safe_path(subfolder, root_name)

    if not folder.is_dir():
        return jsonify({"error": "Folder not found"}), 404

    items = []
    try:
        for entry in sorted(folder.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue

            if entry.is_dir():
                items.append({
                    "name": entry.name,
                    "type": "folder",
                    "path": relative_to_root(entry, root_name),
                })
            elif entry.suffix.lower() in config.ALL_EXTENSIONS:
                stat = entry.stat()
                is_video = entry.suffix.lower() in config.VIDEO_EXTENSIONS
                items.append({
                    "name": entry.name,
                    "type": "video" if is_video else "image",
                    "path": relative_to_root(entry, root_name),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    return jsonify({"items": items, "path": subfolder, "root": root_name})


@app.route("/api/file", methods=["GET"])
@token_required
def get_file():
    rel = request.args.get("path", "")
    root_name = request.args.get("root", "")
    if not rel:
        return jsonify({"error": "path required"}), 400
    target = safe_path(rel, root_name)
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404

    # For images, fix EXIF orientation before serving
    if target.suffix.lower() in config.IMAGE_EXTENSIONS:
        try:
            with Image.open(target) as img:
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGB")
                import io
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=95)
                buf.seek(0)
                return send_file(buf, mimetype="image/jpeg")
        except Exception:
            pass

    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return send_file(target, mimetype=mime)


@app.route("/api/thumbnail", methods=["GET"])
@token_required
def get_thumbnail():
    rel = request.args.get("path", "")
    root_name = request.args.get("root", "")
    size = int(request.args.get("size", 300))
    if not rel:
        return jsonify({"error": "path required"}), 400

    target = safe_path(rel, root_name)
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404

    # For videos, return a placeholder icon (thumbnail generation would need ffmpeg)
    if target.suffix.lower() in config.VIDEO_EXTENSIONS:
        return send_file(target, mimetype=mimetypes.guess_type(str(target))[0] or "video/mp4")

    # Generate / serve cached thumbnail
    cache_key = hashlib.md5(f"{root_name}_{rel}_{size}_{target.stat().st_mtime}".encode()).hexdigest()
    thumb_path = os.path.join(config.THUMBNAIL_FOLDER, f"{cache_key}.jpg")

    if not os.path.exists(thumb_path):
        try:
            with Image.open(target) as img:
                # Fix EXIF orientation (rotated/flipped photos)
                img = ImageOps.exif_transpose(img)
                img = img.convert("RGB")
                img.thumbnail((size, size), Image.LANCZOS)
                img.save(thumb_path, "JPEG", quality=80)
        except Exception:
            return send_file(target, mimetype="image/jpeg")

    return send_file(thumb_path, mimetype="image/jpeg")


@app.route("/api/upload", methods=["POST"])
@token_required
def upload_file():
    # Two upload modes:
    #  * multipart/form-data (legacy) — fields: path, root, file
    #  * raw body (streamed) — the request body IS the file bytes, with metadata
    #    in X-Upload-* headers. The iOS app uses this for photos/videos so it can
    #    stream straight from disk to the network without writing a second
    #    multipart copy of large videos (which tripped the OS disk-write limit).
    from urllib.parse import unquote
    raw_mode = "file" not in request.files
    if raw_mode:
        subfolder = unquote(request.headers.get("X-Upload-Path", ""))
        root_name = unquote(request.headers.get("X-Upload-Root", ""))
        client_name = unquote(request.headers.get("X-Upload-Filename", ""))
    else:
        root_name = request.form.get("root", "")
        client_name = request.files["file"].filename or ""

    # Resolve the ROOT (which shared folder) — the per-file subfolder the client
    # may have sent is intentionally ignored: placement is decided here by the
    # file's capture date so everything is auto-organized into
    # Pictures|Videos/<Year>/<Month>/.
    root_path = get_root_path(root_name)
    if root_path is None:
        abort(404, description=f"Unknown folder: {root_name}")
    base = Path(root_path).resolve()
    base.mkdir(parents=True, exist_ok=True)

    if not client_name:
        return jsonify({"error": "Empty filename"}), 400

    # Sanitize the client-supplied filename. werkzeug.secure_filename strips
    # directory separators and ".." so a malicious filename like
    # "../../../../etc/cron.d/evil" can't escape the upload folder.
    safe_name = secure_filename(client_name)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400

    # Validate extension against the allowlist.
    ext = Path(safe_name).suffix.lower()
    if ext not in config.ALL_EXTENSIONS and ext not in _EXTRA_VIDEO_EXTS:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    # Write to a temp file inside the root first (same filesystem as the final
    # organized folder, so the move below is an atomic rename — no second copy,
    # no extra disk write). We need the bytes on disk before we can read the
    # capture date for organizing.
    incoming = base / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="up_", suffix=ext, dir=str(incoming))
    tmp_path = Path(tmp_name)
    try:
        max_bytes = int(getattr(config, "MAX_UPLOAD_SIZE_MB", 10240)) * 1024 * 1024
        with os.fdopen(fd, "wb") as out:
            if raw_mode:
                # Stream the request body straight to disk in 1 MB chunks — never
                # buffer the whole (possibly multi-GB) upload in server memory.
                # Enforce the size ceiling so an authenticated client can't fill
                # the disk by streaming an endless body.
                written = 0
                while True:
                    chunk = request.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("Upload exceeds the maximum allowed size")
                    out.write(chunk)
            else:
                request.files["file"].save(out)
    except Exception as e:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return jsonify({"error": f"Save failed: {str(e)}"}), 500

    # Magic-byte validation: defense-in-depth against extension spoofing. The
    # extension allowlist lets through anything *named* .jpg / .mp4 even if its
    # content is something else (e.g. a renamed binary). Sniff the header; on
    # mismatch, delete and reject — before it ever lands in the library.
    if not _content_matches_extension(tmp_path, ext):
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return jsonify({"error": f"File content doesn't match extension {ext}"}), 400

    # Decide the destination. With AUTO_ORGANIZE (default) we sort by the file's
    # capture date into Pictures|Videos/<Year>/<Month>/ — components are
    # server-derived only. With it off, fall back to the client-chosen subfolder.
    if getattr(config, "AUTO_ORGANIZE", True):
        is_vid = _is_video_ext(ext)
        date = _capture_date(tmp_path, ext, hint_name=safe_name)
        dest_folder = (base / _organized_subpath(date, is_vid)).resolve()
    else:
        subfolder = ""
        if raw_mode:
            from urllib.parse import unquote as _unq
            subfolder = _unq(request.headers.get("X-Upload-Path", ""))
        else:
            subfolder = request.form.get("path", "")
        dest_folder = safe_path(subfolder, root_name).resolve()
    if not _is_within(dest_folder, base):
        try:
            tmp_path.unlink()
        except Exception:
            pass
        abort(403, description="Resolved upload path is outside the shared folder")
    dest_folder.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_folder, safe_name)
    try:
        os.replace(str(tmp_path), str(dest))
    except OSError:
        shutil.move(str(tmp_path), str(dest))

    return jsonify({
        "success": True,
        "name": dest.name,
        "path": relative_to_root(dest, root_name),
        "root": root_name,
    })


@app.route("/api/folders", methods=["GET"])
@token_required
def list_folders():
    """List all sub-folders recursively for navigation."""
    root_name = request.args.get("root", "")
    root_path = get_root_path(root_name)
    if root_path is None or not os.path.isdir(root_path):
        return jsonify({"folders": [], "root": root_name})
    folders = []
    base = Path(root_path).resolve()
    for root, dirs, _ in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        root_pathobj = Path(root)
        rel = str(root_pathobj.relative_to(base))
        if rel == ".":
            rel = ""
        folders.append(rel)
    return jsonify({"folders": folders, "root": root_name})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_local_ips():
    """Get all local IP addresses that other devices can reach."""
    import socket
    ips = []
    # Method 1: Connect to external address to find the default route IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    # Method 2: Enumerate all interfaces
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips if ips else ["Could not detect IP - check manually"]


def print_banner(started: dict = None):
    ips = get_local_ips()
    started = started or _runtime
    https_port = started.get("https_port")

    print("\n" + "=" * 60)
    print("  Lumina Gallery Server")
    print("=" * 60)
    print(f"  Media folder : {config.MEDIA_FOLDER}")
    print()
    print("  Your server addresses (use any that works):")
    for ip in ips:
        print(f"    -> http://{ip}:{config.PORT}")
        if https_port:
            print(f"    -> https://{ip}:{https_port}  (secure)")
    print()
    print(f"  Access Code  : {config.ACCESS_CODE}")
    if https_port:
        fp = cert_fingerprint()
        if fp:
            print(f"  TLS fingerprint (first 16): {fp[:16]}…")
    print("=" * 60)
    print("  Enter one of the URLs above and the access code")
    print("  in the iOS app to connect.")
    print()

    # Security warnings — weak access codes are the main risk on a LAN.
    if config.ACCESS_CODE == "picture123":
        print("  *** SECURITY WARNING ***")
        print("  You are using the DEFAULT access code 'picture123'.")
        print("  Anyone on your network can connect. Run setup again and")
        print("  choose a private code (8+ characters).")
        print("=" * 60)
        print()
    elif len(str(config.ACCESS_CODE)) < 6:
        print("  *** SECURITY WARNING ***")
        print("  Your access code is short and easy to guess.")
        print("  Use at least 8 characters for a home network.")
        print("=" * 60)
        print()
    print("  TIPS if you can't connect:")
    print("   1. Phone and PC must be on the SAME Wi-Fi network")
    print("   2. Allow Python through Windows Firewall when prompted")
    print("   3. Try each IP address listed above")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    started = start_servers()
    print_banner(started)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nServer stopped.")

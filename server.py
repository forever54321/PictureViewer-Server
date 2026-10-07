#!/usr/bin/env python3
"""
Lumina Gallery Server - Secure media file server for the Lumina Gallery iOS app.
Run this on your Mac or PC to share a folder of photos and videos.

    python server.py                       run the server
    python server.py --organize-dry-run    show what auto-organize WOULD move, move nothing
"""
from __future__ import annotations

import os
import io
import sys
import ssl
import json
import time
import hmac
import stat
import errno
import socket
import shutil
import hashlib
import logging
import argparse
import datetime
import calendar
import tempfile
import ipaddress
import threading
import subprocess
import re as _re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from functools import wraps
from urllib.parse import unquote

from flask import Flask, request, jsonify, send_file, abort, Response
from PIL import Image, ImageOps
import jwt

import config
import safety
from safety import safe_filename, is_cloud_placeholder, is_reparse_or_link

IS_WINDOWS = os.name == "nt"
log = logging.getLogger("lumina")

# ---------------------------------------------------------------------------
# Optional HEIC/HEIF support (pip install -r requirements-heic.txt). The server
# runs fine without it: HEIC thumbnails then fall back to the original bytes,
# which iOS decodes natively.
# ---------------------------------------------------------------------------
HEIF_AVAILABLE = False
try:
    from pillow_heif import register_heif_opener  # type: ignore
    register_heif_opener()
    HEIF_AVAILABLE = True
except Exception:  # ImportError, or a broken native wheel
    pass

# Only these decoders may ever parse untrusted files. Image.MAX_IMAGE_PIXELS is
# deliberately left at Pillow's default (decompression-bomb protection).
PIL_FORMATS = ["JPEG", "PNG", "GIF", "BMP", "WEBP", "TIFF"] + (["HEIF"] if HEIF_AVAILABLE else [])

# Max bytes the app may upload in one request (plus multipart overhead).
_MAX_UPLOAD_BYTES = int(config.MAX_UPLOAD_SIZE_MB) * 1024 * 1024

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = _MAX_UPLOAD_BYTES + 16 * 1024 * 1024
# Non-file multipart fields (path, root) are tiny — keep form memory small.
# File parts are spooled to disk by Werkzeug, not held in memory.
app.config["MAX_FORM_MEMORY_SIZE"] = 1024 * 1024
app.config["MAX_FORM_PARTS"] = 100

# No CORS headers are emitted. The native iOS client does not need CORS;
# omitting it stops browsers from reading this server's responses cross-origin.


@app.before_request
def _block_dns_rebinding():
    """Reject requests whose Host header is a domain name we don't know.

    A DNS-rebinding attack gets the victim's browser to load attacker.com, then
    re-points attacker.com at this server's LAN IP; the browser still sends
    `Host: attacker.com`. Legitimate clients reach this server by IP, so we
    accept IP literals, localhost, *.local (Bonjour) and any names explicitly
    listed in ALLOWED_HOSTS (e.g. a Tailscale MagicDNS name).
    """
    host = (request.host or "").strip().lower()
    if host.startswith("["):                       # [::1]:8500
        host = host[1:].split("]")[0]
    elif host.count(":") == 1:
        host = host.split(":")[0]
    host = host.rstrip(".")
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    for allowed in getattr(config, "ALLOWED_HOSTS", []) or []:
        a = allowed.rstrip(".")
        if (a.startswith(".") and (host.endswith(a) or host == a[1:])) or host == a:
            return
    abort(403, description="Invalid Host header")


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File too large for server", "success": False}), 413


# ---------------------------------------------------------------------------
# Logging — console + rotating file. Never log the access code or tokens.
# ---------------------------------------------------------------------------

_logging_ready = False


def setup_logging():
    global _logging_ready
    if _logging_ready:
        return
    _logging_ready = True
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:   # no ANSI colour codes in request log lines (they end up in the log file)
        import werkzeug.serving as _ws
        _ws._log_add_style = False
    except Exception:
        pass
    if sys.stdout is not None:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(h)
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        fh = RotatingFileHandler(os.path.join(config.LOG_DIR, "server.log"),
                                 maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
    except OSError as e:
        if sys.stderr is not None:
            sys.stderr.write(f"Could not open log file in {config.LOG_DIR}: {e}\n")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _secret() -> str:
    if len(config.SECRET_KEY or "") < 32:
        config.ensure_secret_key()
    return config.SECRET_KEY


def _code_version() -> str:
    """A keyed hash of the CURRENT access code, embedded in every token.
    Changing the access code therefore invalidates all previously issued
    tokens. Keyed with the secret so the (readable) JWT payload can't be used
    to brute-force the code offline."""
    return hmac.new(_secret().encode(), b"access-code:" + str(config.ACCESS_CODE).encode(),
                    hashlib.sha256).hexdigest()[:32]


def create_token():
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "exp": now + datetime.timedelta(hours=config.TOKEN_EXPIRY_HOURS),
        "iat": now,
        "cv": _code_version(),
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def _decode_token(token: str) -> dict:
    payload = jwt.decode(token, _secret(), algorithms=["HS256"],
                         options={"require": ["exp", "iat"]})
    if not hmac.compare_digest(str(payload.get("cv", "")), _code_version()):
        raise jwt.InvalidTokenError("access code changed")
    return payload


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:] if auth_header.startswith("Bearer ") else None
        if not token:
            return jsonify({"error": "Token required"}), 401
        try:
            _decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


def _has_valid_token() -> bool:
    """True if a valid Bearer token is present — without requiring one."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    try:
        _decode_token(auth_header[7:])
        return True
    except jwt.InvalidTokenError:
        return False


# ---------------------------------------------------------------------------
# Shared folders ("roots")
# ---------------------------------------------------------------------------

_root_problem_cache: dict = {}
_reported_bad_roots: set = set()


def root_problem(path: str):
    """Why `path` may not be a shared folder (None = fine). Cached per path."""
    key = str(path)
    if key not in _root_problem_cache:
        _root_problem_cache[key] = safety.root_problem(key, app_dirs=config.app_dirs())
    return _root_problem_cache[key]


def _extra_roots_file() -> str:
    return getattr(config, "FOLDERS_FILE", None) or os.path.join(
        os.path.dirname(os.path.abspath(config.__file__)), "folders.json")


def get_all_configured_roots() -> dict:
    """Every configured root {name: path}, dangerous or not (for reporting)."""
    roots: dict = {}
    if config.MEDIA_FOLDER:
        roots["Library"] = config.MEDIA_FOLDER       # legacy name for the main folder
    cfg_roots = getattr(config, "MEDIA_ROOTS", None)
    if isinstance(cfg_roots, dict):
        for n, p in cfg_roots.items():
            if n and p:
                roots[str(n)] = str(p)
    try:
        with open(_extra_roots_file(), encoding="utf-8-sig") as fh:
            extra = json.load(fh)
        if isinstance(extra, dict):
            for n, p in extra.items():
                if n and p:
                    roots[str(n)] = str(p)
    except Exception:
        pass
    return roots


def get_roots() -> dict:
    """Shared folders as {name: path}: the main media folder, then any extra
    folders (folders.json, read live so added folders appear without a
    restart). Dangerous roots (drive roots, home, system folders, the app's
    own folder) are refused and never served or organized."""
    roots = {}
    for n, p in get_all_configured_roots().items():
        problem = root_problem(p)
        if problem:
            if (n, p) not in _reported_bad_roots:
                _reported_bad_roots.add((n, p))
                log.error("Refusing shared folder '%s' (%s): %s", n, p, problem)
            continue
        roots[n] = p
    return roots


def get_root_path(name: str) -> str | None:
    roots = get_roots()
    if name and name in roots:
        return roots[name]
    if name:
        return None
    return next(iter(roots.values()), None)


# ---------------------------------------------------------------------------
# File-type verification (magic bytes)
# ---------------------------------------------------------------------------

_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm",
                b"hevs", b"mif1", b"msf1", b"avif", b"avis"}
_QT_ATOMS = {b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot", b"uuid"}
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*")


def _sniff_ok(head: bytes, ext: str) -> bool:
    ext = ext.lower()
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == ".gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".bmp":
        return head.startswith(b"BM")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in (".tif", ".tiff", ".dng"):
        return head.startswith(_TIFF_MAGIC)
    if ext in (".heic", ".heif"):
        return head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS
    if ext in (".mp4", ".m4v"):
        return head[4:8] == b"ftyp" and head[8:12].isascii() and head[8:12].strip(b" ").isalnum()
    if ext == ".3gp":
        return head[4:8] == b"ftyp" and head[8:11] in (b"3gp", b"3g2")
    if ext == ".mov":
        return head[4:8] in _QT_ATOMS
    if ext in (".mkv", ".webm"):
        return head.startswith(b"\x1aE\xdf\xa3")
    if ext == ".avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if ext == ".wmv":
        return head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11")
    if ext in (".mts", ".m2ts"):
        # BDAV MPEG-TS: 4-byte timestamp then the 0x47 sync byte, every 192 bytes
        return len(head) >= 197 and head[4] == 0x47 and head[196] == 0x47
    return False   # unknown type: never accept


def _content_matches_extension(path: Path, ext: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(200)
    except OSError:
        return False
    return _sniff_ok(head, ext)


def _is_within(target: Path, base: Path) -> bool:
    """True only if `target` is `base` itself or a descendant of it."""
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
    if os.path.isabs(relative) or (IS_WINDOWS and _re.match(r"^[A-Za-z]:", relative or "")):
        abort(403, description="Absolute paths are not allowed")
    target = (base / relative).resolve()
    if not _is_within(target, base):
        abort(403, description="Path is outside the shared folder")
    return target


def relative_to_root(target: Path, root_name: str) -> str:
    root = get_root_path(root_name) or config.MEDIA_FOLDER
    return str(target.relative_to(Path(root).resolve()))


# ---------------------------------------------------------------------------
# Automatic organization — sort uploads (and, if ORGANIZE_EXISTING, loose
# existing media) into <root>/Photos|Videos/<Year>/<MM-Month>/.
# Folder components come only from the capture date; filenames are kept
# (only made safe with safety.safe_filename). Nothing is ever deleted or
# overwritten.
# ---------------------------------------------------------------------------

_TOP_FOLDERS = ("Photos", "Videos")
_QT_EPOCH = datetime.datetime(1904, 1, 1)
_FILENAME_DATE_RE = _re.compile(r"(20\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])")

# Directory packages we must NEVER walk into or move files out of.
_PACKAGE_DIR_SUFFIXES = (
    ".photoslibrary", ".photolibrary", ".aplibrary", ".migratedaplibrary",
    ".lrlibrary", ".lrdata", ".imovielibrary", ".tvlibrary", ".theater",
    ".fcpbundle", ".musiclibrary", ".app", ".bundle", ".framework",
    ".pkpass", ".rcproject",
)
_SKIP_DIR_NAMES = {"certs", "$recycle.bin", "system volume information", "@eadir",
                   "#recycle", "#snapshot", "lost+found"}


def _is_package_dir(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(s) for s in _PACKAGE_DIR_SUFFIXES)


def _skip_dir(name: str) -> bool:
    return (name.startswith(".") or name.lower() in _SKIP_DIR_NAMES
            or _is_package_dir(name))


def _is_video_ext(ext: str) -> bool:
    return ext.lower() in config.VIDEO_EXTENSIONS


def _is_image_ext(ext: str) -> bool:
    return ext.lower() in config.IMAGE_EXTENSIONS


def _sane_date(d):
    if d is None or d.year < 1990 or d.year > 2100:
        return None
    return d


def _parse_exif_dt(val):
    try:
        return datetime.datetime.strptime(str(val).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        return None


def _open_image(path):
    return Image.open(path, formats=PIL_FORMATS)


def _exif_date(path: Path):
    """DateTimeOriginal (or DateTime) from a photo's EXIF, if present."""
    try:
        with _open_image(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            try:
                sub = exif.get_ifd(0x8769)  # Exif sub-IFD
                for tag in (0x9003, 0x9004):  # DateTimeOriginal, DateTimeDigitized
                    d = _parse_exif_dt(sub.get(tag)) if sub.get(tag) else None
                    if d:
                        return d
            except Exception:
                pass
            for tag in (36867, 306):
                d = _parse_exif_dt(exif.get(tag)) if exif.get(tag) else None
                if d:
                    return d
    except Exception:
        return None
    return None


def _find_atom(f, wanted: bytes, start: int, end: int):
    """Find an MP4/QuickTime box `wanted` between byte offsets [start, end)."""
    pos = start
    for _ in range(100000):
        if pos + 8 > end:
            return None
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        size = int.from_bytes(hdr[0:4], "big")
        atom = hdr[4:8]
        header_len = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return None
            size = int.from_bytes(ext, "big")
            header_len = 16
        elif size == 0:
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
        with open(path, "rb") as f:
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
            if body[0] == 1:
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


_MONTH_TOKEN = {}
for _i in range(1, 13):
    _MONTH_TOKEN[calendar.month_name[_i].lower()] = _i
    _MONTH_TOKEN[calendar.month_abbr[_i].lower()] = _i


def _month_from_token(tok: str):
    t = tok.strip().lower()
    if t in _MONTH_TOKEN:
        return _MONTH_TOKEN[t]
    m = _re.match(r"(\d{1,2})(?:\D|$)", t)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 12:
            return v
    for name, idx in _MONTH_TOKEN.items():
        if len(name) > 3 and name in t:
            return idx
    return None


def _folder_date(path: Path):
    """Infer a date from the Year/Month folder names a file already sits in."""
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
    """EXIF → filename → video metadata → existing Year/Month folder → mtime."""
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
    month = "%02d-%s" % (date.month, calendar.month_name[date.month])   # "01-January"
    return Path(top) / str(date.year) / month


def _final_name(name: str, ext: str) -> str:
    """Sanitized filename that always keeps the (lower-cased check) extension."""
    safe = safe_filename(name)
    if not safe or safe.lower() == ext.lower():
        safe = "file" + ext
    if not safe.lower().endswith(ext.lower()):
        safe = safe + ext
    return safe


def _candidate_names(name: str):
    stem, ext = os.path.splitext(name)
    yield name
    for i in range(1, 100000):
        yield f"{stem}_{i}{ext}"


# One lock serialises "choose a free name + move" for every placement in this
# process (uploads and the organize pass), and the move itself never replaces
# an existing file, so two files can never claim the same destination.
_PLACE_LOCK = threading.Lock()


def _copy_noreplace(src: Path, dest: Path):
    """Cross-device move: exclusive-create the destination, copy, then delete
    the source only after the copy is complete and flushed."""
    with open(src, "rb") as fin, open(dest, "xb") as fout:   # 'x' = O_EXCL
        try:
            shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
        except BaseException:
            fout.close()
            try:
                os.unlink(dest)
            except OSError:
                pass
            raise
    try:
        shutil.copystat(src, dest)
    except OSError:
        pass
    os.unlink(src)


def _atomic_move_noreplace(src: Path, dest: Path):
    """Move src → dest, raising FileExistsError instead of ever overwriting."""
    if IS_WINDOWS:
        try:
            os.rename(src, dest)          # Windows rename never replaces an existing file
            return
        except FileExistsError:
            raise
        except OSError as e:
            if getattr(e, "winerror", None) == 17:   # ERROR_NOT_SAME_DEVICE
                _copy_noreplace(src, dest)
                return
            raise
    try:
        os.link(src, dest)                # atomic; fails if dest exists
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno == errno.EXDEV:
            _copy_noreplace(src, dest)
            return
        # Filesystem without hard links (exFAT, FAT32, some SMB mounts): the
        # check + rename is still race-free within this process thanks to
        # _PLACE_LOCK.
        if os.path.lexists(dest):
            raise FileExistsError(errno.EEXIST, "exists", str(dest))
        try:
            os.rename(src, dest)
        except OSError as e2:
            if e2.errno == errno.EXDEV:
                _copy_noreplace(src, dest)
                return
            raise
        return
    os.unlink(src)


def move_no_overwrite(src: Path, folder: Path, name: str) -> Path:
    """Move `src` into `folder` as `name` (or name_1, name_2 … if taken).
    Never overwrites; safe under concurrent callers."""
    with _PLACE_LOCK:
        for cand in _candidate_names(name):
            dest = folder / cand
            if os.path.lexists(dest):
                continue
            try:
                _atomic_move_noreplace(src, dest)
                return dest
            except FileExistsError:
                continue
    raise OSError(f"No free filename for {name} in {folder}")


def _is_already_organized(src: Path, base: Path) -> bool:
    try:
        rel = src.relative_to(base)
    except ValueError:
        return False
    parts = rel.parts
    if len(parts) != 4:
        return False
    return (parts[0] in _TOP_FOLDERS
            and bool(_re.fullmatch(r"(19|20)\d{2}", parts[1]))
            and bool(_re.fullmatch(r"(0[1-9]|1[0-2])-[A-Za-z]+", parts[2])))


def _iter_media_files(base: Path):
    """Yield (path, stat) for regular media files under base. Never follows
    symlinks/junctions, never enters hidden/system dirs or library packages,
    and never opens cloud placeholder files (stat comes from the directory
    listing itself, which does not recall the file)."""
    stack = [base]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for entry in it:
                name = entry.name
                if name.startswith("."):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if is_reparse_or_link(st):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if not _skip_dir(name) and not is_cloud_placeholder(st):
                        stack.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if os.path.splitext(name)[1].lower() not in config.ALL_EXTENSIONS:
                    continue
                yield Path(entry.path), st


def _plan_destination(src: Path, base: Path):
    ext = src.suffix.lower()
    is_vid = _is_video_ext(ext)
    date = _capture_date(src, ext, hint_name=src.name)
    dest_folder = base / _organized_subpath(date, is_vid)
    if not _is_within(dest_folder, base):
        return None, None
    return dest_folder, _final_name(src.name, ext)


def organize_existing(base_path: str, dry_run: bool = False, report=None):
    """Sort loose/un-organized media already in a root into the structure.

    Idempotent (files already in Photos|Videos/<Year>/<MM-Month>/ are skipped).
    Only moves — never deletes or overwrites. Cloud placeholders, symlinks and
    files whose content doesn't match their extension are left alone. After
    moving, only folders this run moved files OUT of are removed, and only if
    they are now empty (plus parents that became empty because of that).
    With dry_run=True nothing is moved or created; planned moves are passed to
    `report(src, dest)`.
    Returns (moved, skipped)."""
    base = Path(base_path).resolve()
    if not base.is_dir():
        return (0, 0)
    problem = root_problem(str(base))
    if problem:
        raise RuntimeError(f"refusing to organize {base}: {problem}")
    moved = skipped = seen = 0
    moved_from: set = set()
    planned: set = set()
    for src, st in _iter_media_files(base):
        seen += 1
        if seen % 500 == 0:
            log.info("    …%d files checked, %d organized so far", seen, moved)
        if _is_already_organized(src, base):
            skipped += 1
            continue
        if is_cloud_placeholder(st):
            skipped += 1
            continue
        try:
            if not _content_matches_extension(src, src.suffix):
                skipped += 1
                continue
            dest_folder, name = _plan_destination(src, base)
            if dest_folder is None:
                continue
            if src.parent == dest_folder and src.name == name:
                continue
            if dry_run:
                for cand in _candidate_names(name):
                    dest = dest_folder / cand
                    if not os.path.lexists(dest) and dest not in planned:
                        break
                planned.add(dest)
                if report:
                    report(src, dest)
                moved += 1
                continue
            dest_folder.mkdir(parents=True, exist_ok=True)
            move_no_overwrite(src, dest_folder, name)
            moved_from.add(src.parent)
            moved += 1
        except Exception as e:
            # One bad file must never abort the whole pass.
            log.warning("    could not organize %s: %s", src.name, e)
            continue

    # Remove only folders we emptied. os.rmdir refuses non-empty folders, so
    # this can never delete a file; folders the user created empty before this
    # run are never touched (we never moved anything out of them).
    for d in sorted(moved_from, key=lambda p: len(p.parts), reverse=True):
        cur = d
        while cur != base and _is_within(cur, base):
            try:
                os.rmdir(cur)
            except OSError:
                break
            cur = cur.parent
    return (moved, skipped)


def organize_all_roots(dry_run: bool = False):
    if not dry_run and not (config.AUTO_ORGANIZE and config.ORGANIZE_EXISTING):
        return
    log.info("  Organizing existing files into Photos/Videos by year and month…")
    for name, path in get_roots().items():
        try:
            if dry_run:
                print(f"\n  Folder '{name}' ({path}) — planned moves:")
                base = Path(path).resolve()

                def _report(src, dest, base=base):
                    print(f"    {src.relative_to(base)}  ->  {dest.relative_to(base)}")
                moved, skipped = organize_existing(path, dry_run=True, report=_report)
                print(f"  '{name}': {moved} file(s) would be moved, {skipped} left in place.")
            else:
                moved, skipped = organize_existing(path)
                log.info("  '%s': %d file(s) organized, %d already in place/skipped.",
                         name, moved, skipped)
        except Exception as e:
            log.error("  Could not organize folder '%s': %s", name, e)
    if not dry_run:
        log.info("  Organize pass complete.")


def cleanup_stale_incoming(max_age_s: int = 24 * 3600):
    """Delete abandoned partial uploads (.incoming/up_*) older than a day."""
    now = time.time()
    for _name, path in get_roots().items():
        inc = Path(path) / ".incoming"
        try:
            entries = list(os.scandir(inc))
        except OSError:
            continue
        for e in entries:
            if not e.name.startswith("up_"):
                continue
            try:
                st = e.stat(follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and now - st.st_mtime > max_age_s:
                    os.unlink(e.path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# TLS — self-signed certificate, pinned by the iOS app via SHA-256 fingerprint
# ---------------------------------------------------------------------------

_runtime = {"http_port": None, "https_port": None}


def _cert_paths():
    cert_dir = getattr(config, "CERT_DIR", None) or os.path.join(
        os.path.dirname(os.path.abspath(config.THUMBNAIL_FOLDER)), "certs")
    return (cert_dir,
            os.path.join(cert_dir, "server-cert.pem"),
            os.path.join(cert_dir, "server-key.pem"))


def ensure_certificate():
    """Generate a self-signed cert if one doesn't exist yet. Returns
    (cert_path, key_path), or (None, None) without `cryptography`."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        log.warning("Note: 'cryptography' not installed — running HTTP only. "
                    "Run the installer again to enable HTTPS.")
        return None, None

    cert_dir, cert_path, key_path = _cert_paths()
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    os.makedirs(cert_dir, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    san = [x509.DNSName("localhost")]
    hostname = socket.gethostname()
    if hostname:
        try:
            san.append(x509.DNSName(hostname))
            if not hostname.endswith(".local"):
                san.append(x509.DNSName(hostname + ".local"))
        except Exception:
            pass
    seen_ips = set()
    for ip in list(get_lan_ips()) + ["127.0.0.1"]:
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
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    # Key first, owner-only from creation.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


_fp_cache = {"mtime": None, "fp": ""}


def cert_fingerprint() -> str:
    """Lowercase hex SHA-256 fingerprint (full, 64 hex chars) of the server
    certificate. Not a secret — the iOS app pins it. Empty if no cert."""
    _, cert_path, _ = _cert_paths()
    try:
        mtime = os.path.getmtime(cert_path)
    except OSError:
        return ""
    if _fp_cache["mtime"] == mtime:
        return _fp_cache["fp"]
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        fp = cert.fingerprint(hashes.SHA256()).hex()
    except Exception:
        fp = ""
    _fp_cache.update(mtime=mtime, fp=fp)
    return fp


class StartupError(RuntimeError):
    pass


def startup_checks():
    """Everything that must hold before we accept connections."""
    try:
        config.enforce_startup_policy()
    except RuntimeError as e:
        raise StartupError(str(e))
    config.ensure_secret_key()
    if len(config.SECRET_KEY or "") < 32:
        raise StartupError("No usable secret key could be created.")

    main_problem = root_problem(config.MEDIA_FOLDER) if config.MEDIA_FOLDER else "not set"
    if main_problem:
        raise StartupError(
            f"Refusing to share the media folder '{config.MEDIA_FOLDER}': {main_problem}.\n"
            "Run setup again and choose a dedicated photos folder (for example "
            "C:\\Users\\<you>\\Pictures or D:\\Photos).")
    roots = get_roots()          # logs (and drops) any refused extra folders
    if not any(os.path.isdir(p) for p in roots.values()):
        raise StartupError(
            "None of the shared folders are accessible:\n"
            + "\n".join(f"  {n}: {p}" for n, p in roots.items())
            + "\n\nPossible causes: the drive is not connected, or the folder was "
              "renamed/moved. Restore it or run setup again.")


def _background_maintenance():
    time.sleep(2)   # let the startup banner print first
    try:
        cleanup_stale_incoming()
    except Exception as e:
        log.warning("Stale-upload cleanup skipped: %s", e)
    try:
        prune_thumbnail_cache()
    except Exception as e:
        log.warning("Thumbnail cache prune skipped: %s", e)
    try:
        organize_all_roots()
    except Exception as e:
        log.error("Organize pass skipped: %s", e)


def start_servers(http_port: int = None, https_port: int = None):
    """Start the HTTP server, and the HTTPS server too if a cert is available.
    Both run in daemon threads; returns immediately. Raises StartupError."""
    from werkzeug.serving import make_server

    startup_checks()
    os.makedirs(config.THUMBNAIL_FOLDER, exist_ok=True)

    http_port = http_port or config.PORT
    https_port = https_port or getattr(config, "HTTPS_PORT", 8543)

    try:
        http_srv = make_server(config.HOST, http_port, app, threaded=True)
    except OSError as e:
        raise StartupError(f"Could not listen on port {http_port} ({e}). Is the server "
                           "already running, or is another program using that port?")
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    _runtime["http_port"] = http_port

    cert_path, key_path = ensure_certificate()
    if cert_path and key_path:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert_path, key_path)
            https_srv = make_server(config.HOST, https_port, app, threaded=True, ssl_context=ctx)
            threading.Thread(target=https_srv.serve_forever, daemon=True).start()
            _runtime["https_port"] = https_port
        except Exception as e:
            log.error("HTTPS could not start on port %s (%s) — continuing HTTP-only.", https_port, e)

    # Housekeeping + organizing existing files run in the background so a big
    # library never delays startup.
    threading.Thread(target=_background_maintenance, daemon=True, name="maintenance").start()
    return dict(_runtime)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_AUTH_MAX_FAILURES = 5
_AUTH_WINDOW = 300
_auth_failures: dict = {}
_auth_lock = threading.Lock()


def _recent_failures(ip: str, now: float) -> list:
    return [t for t in _auth_failures.get(ip, []) if now - t < _AUTH_WINDOW]


def _request_is_https() -> bool:
    return request.scheme == "https"


@app.route("/api/auth", methods=["POST"])
def authenticate():
    # The access code must not travel in cleartext when TLS is on offer.
    # (The app probes /api/status over HTTP, pins the cert, then authenticates
    # over HTTPS. If HTTPS isn't running, HTTP still works.)
    if _runtime.get("https_port") is not None and not _request_is_https():
        return jsonify({"error": "use https"}), 403

    ip = request.remote_addr or "unknown"
    now = time.time()
    with _auth_lock:
        fails = _recent_failures(ip, now)
        if len(fails) >= _AUTH_MAX_FAILURES:
            retry_in = int(_AUTH_WINDOW - (now - fails[0]))
            return jsonify({"error": f"Too many failed attempts. Try again in {retry_in}s."}), 429

    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    if not isinstance(code, str):
        code = ""

    if config.ACCESS_CODE and hmac.compare_digest(code.encode(), str(config.ACCESS_CODE).encode()):
        with _auth_lock:
            _auth_failures.pop(ip, None)
        return jsonify({"token": create_token()})

    with _auth_lock:
        fails = _recent_failures(ip, now)
        fails.append(now)
        _auth_failures[ip] = fails
        if len(_auth_failures) > 1000:
            for k in [k for k in list(_auth_failures) if not _recent_failures(k, now)]:
                _auth_failures.pop(k, None)
    log.warning("Failed login attempt from %s", ip)
    return jsonify({"error": "Invalid access code"}), 401


@app.route("/api/status", methods=["GET"])
def status():
    folder = config.MEDIA_FOLDER
    authed = _has_valid_token()
    roots = get_roots()
    fp = cert_fingerprint()
    body = {
        "status": "ok",
        "name": "Lumina Gallery Server",
        "version": getattr(config, "SERVER_VERSION", "dev"),
        "media_folder_accessible": bool(folder) and os.path.isdir(folder),
        "https_port": _runtime.get("https_port"),
        "https_available": _runtime.get("https_port") is not None,
        "tls_fingerprint": fp,
        "tls_fingerprint_full": fp,
        "raw_upload": True,
        "auto_organize": bool(config.AUTO_ORGANIZE),
        "root_count": len(roots),
    }
    if authed:
        # Folder names/paths reveal the OS username and layout — only for
        # clients holding a valid token.
        folder_name = os.path.basename(folder.rstrip("\\/")) or folder
        body.update({
            "media_folder": folder,
            "media_folder_name": folder_name,
            "roots": [{"name": n, "path": p, "accessible": os.path.isdir(p)}
                      for n, p in roots.items()],
        })
    else:
        body.update({"media_folder": "", "media_folder_name": "", "roots": []})
    return jsonify(body)


@app.route("/api/roots", methods=["GET"])
@token_required
def list_roots():
    return jsonify({"roots": [{"name": n, "path": p, "accessible": os.path.isdir(p)}
                              for n, p in get_roots().items()]})


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
            try:
                if entry.is_dir():
                    if _is_package_dir(entry.name):
                        continue
                    items.append({"name": entry.name, "type": "folder",
                                  "path": relative_to_root(entry, root_name)})
                elif entry.suffix.lower() in config.ALL_EXTENSIONS:
                    st = entry.stat()
                    is_video = entry.suffix.lower() in config.VIDEO_EXTENSIONS
                    items.append({"name": entry.name, "type": "video" if is_video else "image",
                                  "path": relative_to_root(entry, root_name),
                                  "size": st.st_size, "modified": st.st_mtime})
            except (OSError, ValueError):
                continue
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    return jsonify({"items": items, "path": subfolder, "root": root_name})


_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".bmp": "image/bmp", ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".dng": "image/x-adobe-dng",
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska", ".wmv": "video/x-ms-wmv",
    ".webm": "video/webm", ".3gp": "video/3gpp", ".mts": "video/mp2t", ".m2ts": "video/mp2t",
}


def _media_target(rel: str, root_name: str) -> Path:
    """Resolve a media file for reading; 404 unless it is an allowed media type."""
    target = safe_path(rel, root_name)
    if target.suffix.lower() not in config.ALL_EXTENSIONS:
        abort(404, description="Not a media file")
    if not target.is_file():
        abort(404, description="File not found")
    return target


@app.route("/api/file", methods=["GET"])
@token_required
def get_file():
    rel = request.args.get("path", "")
    root_name = request.args.get("root", "")
    if not rel:
        return jsonify({"error": "path required"}), 400
    target = _media_target(rel, root_name)
    ext = target.suffix.lower()

    # ?display=1 → upright re-encoded JPEG (the pre-2026 behaviour).
    if request.args.get("display") in ("1", "true") and ext in config.IMAGE_EXTENSIONS:
        try:
            with _open_image(target) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=95)
                buf.seek(0)
                return send_file(buf, mimetype="image/jpeg")
        except Exception:
            pass   # fall through to the original bytes

    # Original bytes, with Range / conditional support (video seeking).
    return send_file(target, mimetype=_MIME.get(ext, "application/octet-stream"),
                     conditional=True, etag=True, max_age=0)


# ---- Thumbnails ------------------------------------------------------------

_THUMB_BUCKETS = (128, 256, 400, 512, 800, 1024)
_FFMPEG_TIMEOUT_S = 20
_prune_state = {"last": 0.0, "running": False}
_prune_lock = threading.Lock()


def _thumb_size(raw) -> int | None:
    """Parse ?size= → a bucketed size in 64..1024, or None if invalid."""
    if raw is None or raw == "":
        raw = "300"
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    n = max(64, min(1024, n))
    for b in _THUMB_BUCKETS:
        if n <= b:
            return b
    return _THUMB_BUCKETS[-1]


def _ffmpeg_path():
    return shutil.which("ffmpeg")


def _video_poster(target: Path, size: int, out_path: str) -> bool:
    """Grab one frame with ffmpeg (no shell, fixed args, timeout)."""
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return False
    tmp = out_path + f".{os.getpid()}.{threading.get_ident()}.tmp.jpg"
    flags = 0x08000000 if IS_WINDOWS else 0   # CREATE_NO_WINDOW
    for seek in ("1", "0"):
        cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", seek, "-i", "file:" + str(target), "-frames:v", "1",
               "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
               "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", tmp]
        try:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=_FFMPEG_TIMEOUT_S,
                           check=False, shell=False, creationflags=flags)
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            if os.path.getsize(tmp) > 0:
                os.replace(tmp, out_path)
                return True
        except OSError:
            pass
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return False


def prune_thumbnail_cache(cap_bytes: int | None = None):
    """Keep the thumbnail cache under its size cap, evicting least-recently
    used files first (by max(atime, mtime))."""
    cap = cap_bytes if cap_bytes is not None else int(config.THUMBNAIL_CACHE_MAX_MB) * 1024 * 1024
    folder = config.THUMBNAIL_FOLDER
    entries, total = [], 0
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                total += st.st_size
                entries.append((max(st.st_atime, st.st_mtime), st.st_size, e.path))
    except OSError:
        return 0
    removed = 0
    if total <= cap:
        return 0
    entries.sort()
    target = cap * 0.9
    for _t, size, path in entries:
        if total <= target:
            break
        try:
            os.unlink(path)
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


def _maybe_prune_thumbnails():
    with _prune_lock:
        if _prune_state["running"] or time.time() - _prune_state["last"] < 600:
            return
        _prune_state["running"] = True
        _prune_state["last"] = time.time()

    def run():
        try:
            prune_thumbnail_cache()
        finally:
            _prune_state["running"] = False
    threading.Thread(target=run, daemon=True).start()


@app.route("/api/thumbnail", methods=["GET"])
@token_required
def get_thumbnail():
    rel = request.args.get("path", "")
    root_name = request.args.get("root", "")
    if not rel:
        return jsonify({"error": "path required"}), 400
    size = _thumb_size(request.args.get("size"))
    if size is None:
        return jsonify({"error": "size must be an integer"}), 400

    target = _media_target(rel, root_name)
    ext = target.suffix.lower()
    try:
        st = target.stat()
    except OSError:
        return jsonify({"error": "File not found"}), 404

    os.makedirs(config.THUMBNAIL_FOLDER, exist_ok=True)
    cache_key = hashlib.sha256(
        f"{root_name}\0{rel}\0{size}\0{st.st_mtime_ns}\0{st.st_size}".encode("utf-8", "surrogatepass")
    ).hexdigest()
    thumb_path = os.path.join(config.THUMBNAIL_FOLDER, f"{cache_key}.jpg")
    if os.path.exists(thumb_path):
        return send_file(thumb_path, mimetype="image/jpeg", max_age=0)

    if ext in config.VIDEO_EXTENSIONS:
        # NEVER return the video itself here — a poster frame or nothing.
        if _video_poster(target, size, thumb_path):
            _maybe_prune_thumbnails()
            return send_file(thumb_path, mimetype="image/jpeg", max_age=0)
        return Response(status=204)

    tmp = thumb_path + f".{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with _open_image(target) as img:
            try:
                img.draft("RGB", (size, size))     # fast JPEG downscale on decode
            except Exception:
                pass
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            img.save(tmp, "JPEG", quality=80)
        os.replace(tmp, thumb_path)
        _maybe_prune_thumbnails()
        return send_file(thumb_path, mimetype="image/jpeg", max_age=0)
    except Image.DecompressionBombError:
        return Response(status=204)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        # Pillow can't decode it (e.g. HEIC without pillow-heif, some DNGs):
        # send the original image if it's reasonably small — iOS decodes these
        # formats natively. Otherwise no thumbnail.
        if st.st_size <= 64 * 1024 * 1024 and _content_matches_extension(target, ext):
            return send_file(target, mimetype=_MIME.get(ext, "application/octet-stream"), max_age=0)
        return Response(status=204)


# ---- Upload ------------------------------------------------------------------

def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return 1 << 62    # unknown — don't block the upload on it


def _min_free_bytes() -> int:
    return int(getattr(config, "MIN_FREE_SPACE_MB", 1024)) * 1024 * 1024


def _insufficient_space():
    return jsonify({"error": "Not enough free disk space on the server", "success": False}), 507


@app.route("/api/upload", methods=["POST"])
@token_required
def upload_file():
    # Two upload modes:
    #  * multipart/form-data (legacy) — fields: path, root, file
    #  * raw body (streamed) — body IS the file, metadata in X-Upload-* headers.
    is_multipart = request.mimetype == "multipart/form-data"
    if is_multipart:
        root_name = None  # read after the space check (parsing spools the body)
    else:
        root_name = unquote(request.headers.get("X-Upload-Root", ""))

    # Free-space check BEFORE accepting the body.
    pre_root = root_name if root_name is not None else request.args.get("root", "")
    pre_path = get_root_path(pre_root or "") or get_root_path("")
    if pre_path:
        need = (request.content_length or 0) + _min_free_bytes()
        if _free_bytes(Path(pre_path)) < need:
            return _insufficient_space()

    if is_multipart:
        if "file" not in request.files:
            return jsonify({"error": "No file in upload"}), 400
        root_name = request.form.get("root", "")
        client_name = request.files["file"].filename or ""
    else:
        client_name = unquote(request.headers.get("X-Upload-Filename", ""))

    root_path = get_root_path(root_name)
    if root_path is None:
        abort(404, description=f"Unknown folder: {root_name}")
    base = Path(root_path).resolve()
    if not base.is_dir():
        return jsonify({"error": "The shared folder is not accessible"}), 404

    if not client_name:
        return jsonify({"error": "Empty filename"}), 400
    # Keep the original filename; only strip separators / reserved characters.
    safe_name = safe_filename(client_name)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400
    ext = Path(safe_name).suffix.lower()
    if ext not in config.ALL_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    if _free_bytes(base) < (request.content_length or 0) + _min_free_bytes():
        return _insufficient_space()

    # Temp file inside the root (same filesystem → the final move is a rename).
    incoming = base / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="up_", suffix=ext, dir=str(incoming))
    tmp_path = Path(tmp_name)

    def _discard():
        try:
            tmp_path.unlink()
        except OSError:
            pass

    try:
        with os.fdopen(fd, "wb") as out:
            if not is_multipart:
                written, since_check = 0, 0
                while True:
                    chunk = request.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    since_check += len(chunk)
                    if written > _MAX_UPLOAD_BYTES:
                        raise ValueError("Upload exceeds the maximum allowed size")
                    if since_check >= 256 * 1024 * 1024:     # chunked bodies: re-check space
                        since_check = 0
                        if _free_bytes(base) < _min_free_bytes():
                            _discard()
                            return _insufficient_space()
                    out.write(chunk)
            else:
                request.files["file"].save(out)
    except Exception as e:
        _discard()
        log.warning("Upload failed: %s", e)
        return jsonify({"error": f"Save failed: {e}", "success": False}), 500

    if not _content_matches_extension(tmp_path, ext):
        _discard()
        return jsonify({"error": f"File content doesn't match extension {ext}"}), 400

    if config.AUTO_ORGANIZE:
        date = _capture_date(tmp_path, ext, hint_name=safe_name)
        dest_folder = base / _organized_subpath(date, _is_video_ext(ext))
    else:
        if is_multipart:
            subfolder = request.form.get("path", "")
        else:
            subfolder = unquote(request.headers.get("X-Upload-Path", ""))
        dest_folder = safe_path(subfolder, root_name)
    if not _is_within(dest_folder, base):
        _discard()
        abort(403, description="Resolved upload path is outside the shared folder")
    try:
        dest_folder.mkdir(parents=True, exist_ok=True)
        dest = move_no_overwrite(tmp_path, dest_folder, safe_name)
    except OSError as e:
        _discard()
        return jsonify({"error": f"Save failed: {e}", "success": False}), 500

    return jsonify({
        "success": True,
        "name": dest.name,
        "path": relative_to_root(dest.resolve(), root_name),
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
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        rel = str(Path(root).relative_to(base))
        folders.append("" if rel == "." else rel)
    return jsonify({"folders": folders, "root": root_name})


# ---------------------------------------------------------------------------
# Network addresses + banner
# ---------------------------------------------------------------------------

_CGNAT = ipaddress.ip_network("100.64.0.0/10")   # Tailscale and similar


def _is_lan_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version != 4 or a.is_loopback or a.is_link_local or a.is_multicast:
        return False
    return a.is_private or a in _CGNAT


def get_lan_ips() -> list:
    """LAN addresses phones can reach: the address of the adapter that holds
    the default route first, then other private-range addresses."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("192.0.2.1", 80))     # UDP connect sends no packets
        ip = s.getsockname()[0]
        s.close()
        if _is_lan_ip(ip):
            ips.append(ip)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and _is_lan_ip(ip):
                ips.append(ip)
    except Exception:
        pass
    return ips


# Back-compat alias (older launchers import this name).
get_local_ips = get_lan_ips


def print_banner(started: dict = None):
    started = started or _runtime
    http_port = started.get("http_port") or config.PORT
    https_port = started.get("https_port")
    ips = get_lan_ips()
    lines = ["", "=" * 64, f"  Lumina Gallery Server {getattr(config, 'SERVER_VERSION', '')}", "=" * 64]
    roots = get_roots()
    for n, p in roots.items():
        lines.append(f"  Shared folder '{n}': {p}")
    lines.append(f"  Auto-organize: {'on' if config.AUTO_ORGANIZE else 'off'}"
                 f"{' (existing files too)' if config.AUTO_ORGANIZE and config.ORGANIZE_EXISTING else ''}")
    lines.append("")
    if ips:
        lines.append("  Server address for the iPhone app (try the first one first):")
        for ip in ips:
            lines.append(f"    -> http://{ip}:{http_port}")
    else:
        lines.append("  Could not detect a LAN address — run 'ipconfig' (Windows) or")
        lines.append("  'ifconfig' (macOS/Linux) and use this computer's Wi-Fi/Ethernet IPv4.")
    if https_port:
        lines.append(f"  Secure port (used automatically by the app): {https_port}")
        fp = cert_fingerprint()
        if fp:
            lines.append("  TLS certificate fingerprint (SHA-256):")
            lines.append("    " + ":".join(fp[i:i + 2] for i in range(0, len(fp), 2)).upper())
    lines.append("")
    lines.append("  Access code: not shown here. To display it use the Status tool")
    lines.append("  (Windows: Start Menu > 'Lumina Server - Status'; macOS: ~/Applications/")
    lines.append("  Lumina Server; Linux: 'lumina-server status'), or see config.json / .env.")
    lines.append("=" * 64)
    lines.append("  If the phone can't connect: same Wi-Fi network, the network set to")
    lines.append("  'Private' in Windows, and the firewall rule from the installer.")
    lines.append("=" * 64)
    for ln in lines:
        log.info(ln)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Lumina Gallery Server")
    parser.add_argument("--organize-dry-run", action="store_true",
                        help="print the moves auto-organize would make, then exit (moves nothing)")
    args = parser.parse_args(argv)

    setup_logging()
    if args.organize_dry_run:
        roots = get_roots()
        if not roots:
            print("No usable shared folders are configured.")
            return 2
        if not (config.AUTO_ORGANIZE and config.ORGANIZE_EXISTING):
            print("Note: organizing existing files is currently OFF in your settings; "
                  "this shows what it would do if turned on.")
        organize_all_roots(dry_run=True)
        print("\nDry run only — nothing was moved.")
        return 0

    try:
        started = start_servers()
    except StartupError as e:
        log.error("\nERROR: %s\n", e)
        return 2
    print_banner(started)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("\nServer stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

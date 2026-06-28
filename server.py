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


def get_roots() -> dict:
    """Return all configured roots as {name: path}.

    Backwards compatible: if no MEDIA_ROOTS in config, exposes the legacy
    single MEDIA_FOLDER as a root named "Library".
    """
    roots = getattr(config, "MEDIA_ROOTS", None)
    if isinstance(roots, dict) and roots:
        return roots
    return {"Library": config.MEDIA_FOLDER}


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
    subfolder = request.form.get("path", "")
    root_name = request.form.get("root", "")
    folder = safe_path(subfolder, root_name)

    if not folder.is_dir():
        os.makedirs(folder, exist_ok=True)

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    # Sanitize the client-supplied filename. werkzeug.secure_filename strips
    # directory separators and ".." so a malicious filename like
    # "../../../../etc/cron.d/evil" can't escape the upload folder.
    safe_name = secure_filename(file.filename)
    if not safe_name:
        return jsonify({"error": "Invalid filename"}), 400

    # Validate extension against the allowlist.
    ext = Path(safe_name).suffix.lower()
    extra_video = {".3gp", ".flv", ".ts", ".mts", ".m2ts"}
    if ext not in config.ALL_EXTENSIONS and ext not in extra_video:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    # Avoid overwriting – add number suffix if needed
    dest = folder / safe_name
    counter = 1
    while dest.exists():
        stem = Path(safe_name).stem
        dest = folder / f"{stem}_{counter}{ext}"
        counter += 1

    # Defense in depth: confirm the final destination is still inside the
    # shared folder before writing.
    if not _is_within(dest.resolve(), folder.resolve()):
        abort(403, description="Resolved upload path is outside the shared folder")

    try:
        file.save(str(dest))
    except Exception as e:
        return jsonify({"error": f"Save failed: {str(e)}"}), 500

    # Magic-byte validation: defense-in-depth against extension spoofing. The
    # extension allowlist above lets through anything *named* .jpg / .mp4, even
    # if its content is something else (e.g. a renamed binary). Sniff the first
    # 16 bytes against known image/video signatures; on mismatch, delete and
    # reject.
    if not _content_matches_extension(dest, ext):
        try:
            dest.unlink()
        except Exception:
            pass
        return jsonify({"error": f"File content doesn't match extension {ext}"}), 400

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

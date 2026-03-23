#!/usr/bin/env python3
"""
PictureViewer Server - Secure media file server for the PictureViewer iOS app.
Run this on your Mac or PC to share a folder of photos and videos.
"""

import os
import sys
import json
import hashlib
import datetime
import mimetypes
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, send_file, abort
from flask_cors import CORS
from PIL import Image, ImageOps
import jwt

import config

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024
CORS(app)

# Ensure directories exist
os.makedirs(config.THUMBNAIL_FOLDER, exist_ok=True)
os.makedirs(config.MEDIA_FOLDER, exist_ok=True)


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


def safe_path(relative: str) -> Path:
    """Resolve a relative path inside MEDIA_FOLDER and reject traversal."""
    base = Path(config.MEDIA_FOLDER).resolve()
    target = (base / relative).resolve()
    if not str(target).startswith(str(base)):
        abort(403)
    return target


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/api/auth", methods=["POST"])
def authenticate():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    if code == config.ACCESS_CODE:
        return jsonify({"token": create_token()})
    return jsonify({"error": "Invalid access code"}), 401


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({"status": "ok", "name": "PictureViewer Server"})


@app.route("/api/files", methods=["GET"])
@token_required
def list_files():
    subfolder = request.args.get("path", "")
    folder = safe_path(subfolder)

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
                    "path": str(entry.relative_to(Path(config.MEDIA_FOLDER).resolve())),
                })
            elif entry.suffix.lower() in config.ALL_EXTENSIONS:
                stat = entry.stat()
                is_video = entry.suffix.lower() in config.VIDEO_EXTENSIONS
                rel = str(entry.relative_to(Path(config.MEDIA_FOLDER).resolve()))
                items.append({
                    "name": entry.name,
                    "type": "video" if is_video else "image",
                    "path": rel,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403

    return jsonify({"items": items, "path": subfolder})


@app.route("/api/file", methods=["GET"])
@token_required
def get_file():
    rel = request.args.get("path", "")
    if not rel:
        return jsonify({"error": "path required"}), 400
    target = safe_path(rel)
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
    size = int(request.args.get("size", 300))
    if not rel:
        return jsonify({"error": "path required"}), 400

    target = safe_path(rel)
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404

    # For videos, return a placeholder icon (thumbnail generation would need ffmpeg)
    if target.suffix.lower() in config.VIDEO_EXTENSIONS:
        return send_file(target, mimetype=mimetypes.guess_type(str(target))[0] or "video/mp4")

    # Generate / serve cached thumbnail
    cache_key = hashlib.md5(f"{rel}_{size}_{target.stat().st_mtime}".encode()).hexdigest()
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
    folder = safe_path(subfolder)

    if not folder.is_dir():
        os.makedirs(folder, exist_ok=True)

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    # Validate extension
    ext = Path(file.filename).suffix.lower()
    if ext not in config.ALL_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    # Avoid overwriting – add number suffix if needed
    dest = folder / file.filename
    counter = 1
    while dest.exists():
        stem = Path(file.filename).stem
        dest = folder / f"{stem}_{counter}{ext}"
        counter += 1

    file.save(str(dest))
    return jsonify({
        "success": True,
        "name": dest.name,
        "path": str(dest.relative_to(Path(config.MEDIA_FOLDER).resolve())),
    })


@app.route("/api/folders", methods=["GET"])
@token_required
def list_folders():
    """List all sub-folders recursively for navigation."""
    folders = []
    base = Path(config.MEDIA_FOLDER).resolve()
    for root, dirs, _ in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        root_path = Path(root)
        rel = str(root_path.relative_to(base))
        if rel == ".":
            rel = ""
        folders.append(rel)
    return jsonify({"folders": folders})


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


def print_banner():
    ips = get_local_ips()

    print("\n" + "=" * 60)
    print("  PictureViewer Server")
    print("=" * 60)
    print(f"  Media folder : {config.MEDIA_FOLDER}")
    print()
    print("  Your server addresses (use any that works):")
    for ip in ips:
        print(f"    -> http://{ip}:{config.PORT}")
    print()
    print(f"  Access Code  : {config.ACCESS_CODE}")
    print("=" * 60)
    print("  Enter one of the URLs above and the access code")
    print("  in the iOS app to connect.")
    print()
    print("  TIPS if you can't connect:")
    print("   1. Phone and PC must be on the SAME Wi-Fi network")
    print("   2. Allow Python through Windows Firewall when prompted")
    print("   3. Try each IP address listed above")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    print_banner()
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)

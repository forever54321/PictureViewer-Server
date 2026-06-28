import os
import secrets
from pathlib import Path

# Server Configuration
HOST = "0.0.0.0"
PORT = 8500              # plain HTTP (kept during the HTTPS transition)
HTTPS_PORT = 8543        # TLS — self-signed cert, pinned by the iOS app

# Media folder - change this to your desired folder path
MEDIA_FOLDER = os.environ.get("PICTUREVIEWER_MEDIA_FOLDER", str(Path.home() / "Pictures"))

# Multiple shared roots — populated by the launcher from config.json. The iOS
# app picks which root to view/upload to. Empty/unset means single-root mode
# using MEDIA_FOLDER as a root named "Library".
MEDIA_ROOTS: dict = {}

# Thumbnail cache
THUMBNAIL_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".thumbnails")

# Supported file extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif", ".tiff", ".tif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Authentication
SECRET_KEY = os.environ.get("PICTUREVIEWER_SECRET_KEY", secrets.token_hex(32))
ACCESS_CODE = os.environ.get("PICTUREVIEWER_ACCESS_CODE", "picture123")
# Refuse to run with the well-known default; LAN attackers will try it first.
# Override via PICTUREVIEWER_ACCESS_CODE env var or config.json.
if ACCESS_CODE == "picture123":
    raise RuntimeError(
        "Refusing to start: PICTUREVIEWER_ACCESS_CODE is the default 'picture123'. "
        "Set the PICTUREVIEWER_ACCESS_CODE env var to a strong value before launch."
    )
TOKEN_EXPIRY_HOURS = 720  # 30 days

# Upload settings
MAX_UPLOAD_SIZE_MB = 10240  # 10GB max

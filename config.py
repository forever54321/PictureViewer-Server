import os
import secrets
from pathlib import Path

# Load the .env that the installer wrote (PICTUREVIEWER_* settings) from the
# folder this file lives in — BEFORE reading any of those values below. This
# makes the server pick up its media folder / access code / secret no matter how
# it was launched (start script, double-click, or a Windows auto-start task that
# runs with a different working directory and no inherited environment). Without
# this, those launch methods would fall back to the default access code, which
# the policy check below rejects — so the server would exit immediately.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

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


def validate_access_code(code: str):
    """Enforce the access-code policy. Returns None if OK, else a short reason.

    Policy: at least 12 characters AND at least one lowercase letter, one
    uppercase letter, one number, and one special (non-alphanumeric) character.
    This is the credential used to connect to the server and upload photos, so
    it must resist brute-forcing by anyone on the network.
    """
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
    return None


# Refuse to start with an access code that doesn't meet the policy (this also
# rejects the old default 'picture123'). Enforced here so the rule holds no
# matter how the code was supplied — installer, env var, or config.json.
_code_problem = validate_access_code(ACCESS_CODE)
if _code_problem:
    raise RuntimeError(
        "Refusing to start: the access code does not meet the security policy "
        f"(it must contain {_code_problem}). Choose a code of at least 12 "
        "characters with lowercase, uppercase, a number, and a special "
        "character, then set PICTUREVIEWER_ACCESS_CODE (or run setup again)."
    )
TOKEN_EXPIRY_HOURS = 720  # 30 days

# Upload settings
MAX_UPLOAD_SIZE_MB = 10240  # 10GB max

# Automatic organization. When True, uploaded files (and any loose existing
# files found at startup) are sorted into:
#     Photos/<Year>/<MM-Month>/    e.g. Photos/2026/01-January/
#     Videos/<Year>/<MM-Month>/    e.g. Videos/2026/01-January/
# The date is taken from the photo's EXIF / the video's metadata, falling back
# to the filename date, then the file's modified time. Set to False to keep
# uploads in the folder the app chose instead.
AUTO_ORGANIZE = os.environ.get("PICTUREVIEWER_AUTO_ORGANIZE", "1") not in ("0", "false", "False", "")

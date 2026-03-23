import os
import secrets
from pathlib import Path

# Server Configuration
HOST = "0.0.0.0"
PORT = 8500

# Media folder - change this to your desired folder path
MEDIA_FOLDER = os.environ.get("PICTUREVIEWER_MEDIA_FOLDER", str(Path.home() / "Pictures"))

# Thumbnail cache
THUMBNAIL_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".thumbnails")

# Supported file extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif", ".tiff", ".tif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Authentication
SECRET_KEY = os.environ.get("PICTUREVIEWER_SECRET_KEY", secrets.token_hex(32))
ACCESS_CODE = os.environ.get("PICTUREVIEWER_ACCESS_CODE", "picture123")
TOKEN_EXPIRY_HOURS = 720  # 30 days

# Upload settings
MAX_UPLOAD_SIZE_MB = 500

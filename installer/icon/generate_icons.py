#!/usr/bin/env python3
"""Generate .ico and .icns from AppIcon.png"""
import os
import shutil
import subprocess
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "..", "..", "..", "PictureViewer", "PictureViewer",
                   "Assets.xcassets", "AppIcon.appiconset", "AppIcon.png")

def make_ico():
    img = Image.open(SRC)
    ico_path = os.path.join(SCRIPT_DIR, "AppIcon.ico")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(ico_path, format="ICO", sizes=sizes)
    print(f"Created {ico_path}")

def make_icns():
    iconset = os.path.join(SCRIPT_DIR, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    img = Image.open(SRC)
    for size in [16, 32, 64, 128, 256, 512, 1024]:
        resized = img.resize((size, size), Image.LANCZOS)
        resized.save(os.path.join(iconset, f"icon_{size}x{size}.png"))
        if size <= 512:
            double = img.resize((size * 2, size * 2), Image.LANCZOS)
            double.save(os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
    icns_path = os.path.join(SCRIPT_DIR, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns_path], check=True)
    shutil.rmtree(iconset)
    print(f"Created {icns_path}")

if __name__ == "__main__":
    make_ico()
    make_icns()

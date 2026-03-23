#!/bin/bash
# Build PictureViewer Server .app bundle and .dmg for macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ICON_DIR="$SCRIPT_DIR/../icon"
APP_NAME="PictureViewer Server"
APP_BUNDLE="$SCRIPT_DIR/$APP_NAME.app"
DMG_NAME="PictureViewerServer.dmg"

echo "Building $APP_NAME..."

# Clean previous build
rm -rf "$APP_BUNDLE" "$SCRIPT_DIR/$DMG_NAME"

# Create .app structure
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# Copy server files
cp "$SERVER_DIR/server.py" "$APP_BUNDLE/Contents/Resources/"
cp "$SERVER_DIR/config.py" "$APP_BUNDLE/Contents/Resources/"
cp "$SERVER_DIR/requirements.txt" "$APP_BUNDLE/Contents/Resources/"

# Copy icon
if [ -f "$ICON_DIR/AppIcon.icns" ]; then
    cp "$ICON_DIR/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/"
fi

# Copy setup wizard
cp "$SCRIPT_DIR/setup_wizard.py" "$APP_BUNDLE/Contents/Resources/"

# Create Info.plist
cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>PictureViewer Server</string>
    <key>CFBundleDisplayName</key>
    <string>PictureViewer Server</string>
    <key>CFBundleIdentifier</key>
    <string>com.pictureviewer.server</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>PictureViewerServer</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# Create launcher script
cat > "$APP_BUNDLE/Contents/MacOS/PictureViewerServer" <<'LAUNCHER'
#!/bin/bash
# PictureViewer Server Launcher
RESOURCES="$(dirname "$0")/../Resources"
APP_SUPPORT="$HOME/Library/Application Support/PictureViewer"
CONFIG_FILE="$APP_SUPPORT/config.json"
VENV_DIR="$APP_SUPPORT/venv"

mkdir -p "$APP_SUPPORT"

# Check for Python 3
PYTHON=""
for cmd in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$cmd" &> /dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "Python 3 Required" message "Please install Python 3 from python.org or via Homebrew:\n\nbrew install python3" as critical'
    open "https://www.python.org/downloads/"
    exit 1
fi

# First run — setup venv and show wizard
if [ ! -f "$CONFIG_FILE" ] || [ ! -d "$VENV_DIR" ]; then
    # Create venv
    osascript -e 'display notification "Setting up PictureViewer Server..." with title "PictureViewer"'
    "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null

    # Install dependencies
    "$VENV_DIR/bin/pip" install --upgrade pip -q 2>/dev/null
    "$VENV_DIR/bin/pip" install -r "$RESOURCES/requirements.txt" -q 2>/dev/null

    # Run setup wizard
    "$VENV_DIR/bin/python3" "$RESOURCES/setup_wizard.py"

    if [ ! -f "$CONFIG_FILE" ]; then
        exit 0  # User cancelled
    fi
fi

# Load config and start server
export PYTHONPATH="$RESOURCES"
cd "$RESOURCES"

"$VENV_DIR/bin/python3" -c "
import json, os, sys
sys.path.insert(0, '$RESOURCES')

config_path = '$CONFIG_FILE'
with open(config_path) as f:
    cfg = json.load(f)

os.environ['PICTUREVIEWER_MEDIA_FOLDER'] = cfg['media_folder']
os.environ['PICTUREVIEWER_ACCESS_CODE'] = cfg['access_code']
os.environ['PICTUREVIEWER_SECRET_KEY'] = cfg['secret_key']

import config as srv_config
srv_config.MEDIA_FOLDER = cfg['media_folder']
srv_config.ACCESS_CODE = cfg['access_code']
srv_config.SECRET_KEY = cfg['secret_key']
srv_config.PORT = cfg.get('port', 8500)
srv_config.THUMBNAIL_FOLDER = os.path.join('$APP_SUPPORT', 'thumbnails')

os.makedirs(srv_config.THUMBNAIL_FOLDER, exist_ok=True)

import server
server.print_banner()
server.app.run(host='0.0.0.0', port=srv_config.PORT, debug=False, threaded=True)
"
LAUNCHER

chmod +x "$APP_BUNDLE/Contents/MacOS/PictureViewerServer"

# Create DMG
echo "Creating DMG..."
DMG_STAGING="$SCRIPT_DIR/dmg_staging"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP_BUNDLE" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"

hdiutil create \
    -volname "PictureViewer Server" \
    -srcfolder "$DMG_STAGING" \
    -ov -format UDZO \
    "$SCRIPT_DIR/$DMG_NAME"

rm -rf "$DMG_STAGING"

echo ""
echo "============================================================"
echo "  Build complete!"
echo "  App: $APP_BUNDLE"
echo "  DMG: $SCRIPT_DIR/$DMG_NAME"
echo "============================================================"

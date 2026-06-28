#!/bin/bash
# ============================================================
# PictureViewer Server - macOS Installer
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo ""
echo "============================================================"
echo "  PictureViewer Server - macOS Installer"
echo "============================================================"
echo ""

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is required but not installed."
    echo "Install it from https://www.python.org/downloads/ or via Homebrew:"
    echo "  brew install python3"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "Found $PYTHON_VERSION"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv "$VENV_DIR"

# Activate and install dependencies
echo "Installing dependencies..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "Dependencies installed successfully."

# Ask for configuration
echo ""
echo "--- Configuration ---"
echo ""

read -p "Media folder path [~/Pictures]: " MEDIA_FOLDER
MEDIA_FOLDER="${MEDIA_FOLDER:-$HOME/Pictures}"
# Expand a leading ~ to $HOME WITHOUT eval (eval would execute $(...)/backticks
# embedded in the path — a command-injection hole).
MEDIA_FOLDER="${MEDIA_FOLDER/#\~/$HOME}"

read -p "Server port [8500]: " PORT
PORT="${PORT:-8500}"

# Require a strong, non-default access code. The server refuses to start with
# the well-known default, and a short code is trivially brute-forced on a LAN.
while :; do
    read -p "Choose an access code (12+ chars: lowercase, UPPERCASE, number, special): " ACCESS_CODE
    if [ "${#ACCESS_CODE}" -ge 12 ] \
       && printf '%s' "$ACCESS_CODE" | grep -q '[a-z]' \
       && printf '%s' "$ACCESS_CODE" | grep -q '[A-Z]' \
       && printf '%s' "$ACCESS_CODE" | grep -q '[0-9]' \
       && printf '%s' "$ACCESS_CODE" | grep -q '[^a-zA-Z0-9]'; then
        break
    fi
    echo "  -> Must be 12+ characters with a lowercase letter, an uppercase letter, a number, and a special character."
done

# Create .env file with the secret key and access code. umask 177 makes it
# owner-read/write only (0600) so other local users can't read the secrets.
( umask 177; cat > "$SCRIPT_DIR/.env" <<EOL
PICTUREVIEWER_MEDIA_FOLDER=$MEDIA_FOLDER
PICTUREVIEWER_ACCESS_CODE=$ACCESS_CODE
PICTUREVIEWER_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
EOL
)
chmod 600 "$SCRIPT_DIR/.env"

# Update port in config if changed
if [ "$PORT" != "8500" ]; then
    sed -i '' "s/PORT = 8500/PORT = $PORT/" "$SCRIPT_DIR/config.py"
fi

# Create launch script
cat > "$SCRIPT_DIR/start_server.command" <<EOL
#!/bin/bash
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
set -a; . "$SCRIPT_DIR/.env"; set +a
python3 server.py
EOL
chmod +x "$SCRIPT_DIR/start_server.command"

# Create LaunchAgent for auto-start (optional)
echo ""
read -p "Start server automatically on login? [y/N]: " AUTO_START

if [[ "$AUTO_START" =~ ^[Yy]$ ]]; then
    PLIST_PATH="$HOME/Library/LaunchAgents/com.pictureviewer.server.plist"
    cat > "$PLIST_PATH" <<EOL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pictureviewer.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python3</string>
        <string>$SCRIPT_DIR/server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PICTUREVIEWER_MEDIA_FOLDER</key>
        <string>$MEDIA_FOLDER</string>
        <key>PICTUREVIEWER_ACCESS_CODE</key>
        <string>$ACCESS_CODE</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/server.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/server_error.log</string>
</dict>
</plist>
EOL
    chmod 600 "$PLIST_PATH"  # contains the access code — owner-only
    launchctl load "$PLIST_PATH" 2>/dev/null || true
    echo "Server registered as login item."
fi

# Get IP address
IP_ADDRESS=$(ipconfig getifaddr en0 2>/dev/null || echo "unknown")

echo ""
echo "============================================================"
echo "  Installation Complete!"
echo "============================================================"
echo ""
echo "  Media Folder  : $MEDIA_FOLDER"
echo "  Server URL    : http://$IP_ADDRESS:$PORT"
echo "  Access Code   : $ACCESS_CODE"
echo ""
echo "  To start the server:"
echo "    Double-click: start_server.command"
echo "    Or run:       cd $SCRIPT_DIR && ./start_server.command"
echo ""
echo "  Enter the URL and access code in the iOS app to connect."
echo "============================================================"
echo ""

# Ask to start now
read -p "Start the server now? [Y/n]: " START_NOW
if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then
    source "$VENV_DIR/bin/activate"
    set -a; . "$SCRIPT_DIR/.env"; set +a
    python3 "$SCRIPT_DIR/server.py"
fi

#!/bin/bash
# ============================================================
# PictureViewer Server - Linux Installer
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo ""
echo "============================================================"
echo "  PictureViewer Server - Linux Installer"
echo "============================================================"
echo ""

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is required but not installed."
    echo "Install it with your package manager:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "  Fedora/RHEL:   sudo dnf install python3 python3-pip"
    echo "  Arch:          sudo pacman -S python python-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "Found $PYTHON_VERSION"

# Check for venv module
if ! python3 -m venv --help &> /dev/null 2>&1; then
    echo ""
    echo "ERROR: python3-venv is required. Install it:"
    echo "  Ubuntu/Debian: sudo apt install python3-venv"
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv "$VENV_DIR"

# Install dependencies
echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q
echo "Dependencies installed successfully."

# Configuration
echo ""
echo "--- Configuration ---"
echo ""

read -p "Media folder path [$HOME/Pictures]: " MEDIA_FOLDER
MEDIA_FOLDER="${MEDIA_FOLDER:-$HOME/Pictures}"
MEDIA_FOLDER=$(eval echo "$MEDIA_FOLDER")

read -p "Server port [8500]: " PORT
PORT="${PORT:-8500}"

read -p "Access code [picture123]: " ACCESS_CODE
ACCESS_CODE="${ACCESS_CODE:-picture123}"

# Create media folder if needed
mkdir -p "$MEDIA_FOLDER"

# Generate secret key
SECRET_KEY=$("$VENV_DIR/bin/python3" -c "import secrets; print(secrets.token_hex(32))")

# Create .env file
cat > "$SCRIPT_DIR/.env" <<EOL
PICTUREVIEWER_MEDIA_FOLDER=$MEDIA_FOLDER
PICTUREVIEWER_ACCESS_CODE=$ACCESS_CODE
PICTUREVIEWER_SECRET_KEY=$SECRET_KEY
EOL

# Update port if changed
if [ "$PORT" != "8500" ]; then
    sed -i "s/PORT = 8500/PORT = $PORT/" "$SCRIPT_DIR/config.py"
fi

# Create start script
cat > "$SCRIPT_DIR/start_server.sh" <<EOL
#!/bin/bash
cd "$SCRIPT_DIR"
export \$(grep -v '^#' .env | xargs)
"$VENV_DIR/bin/python3" server.py
EOL
chmod +x "$SCRIPT_DIR/start_server.sh"

# Optional: systemd service
echo ""
read -p "Install as systemd service (auto-start on boot)? [y/N]: " INSTALL_SERVICE

if [[ "$INSTALL_SERVICE" =~ ^[Yy]$ ]]; then
    SERVICE_FILE="/etc/systemd/system/pictureviewer.service"
    sudo tee "$SERVICE_FILE" > /dev/null <<EOL
[Unit]
Description=PictureViewer Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
Environment=PICTUREVIEWER_MEDIA_FOLDER=$MEDIA_FOLDER
Environment=PICTUREVIEWER_ACCESS_CODE=$ACCESS_CODE
Environment=PICTUREVIEWER_SECRET_KEY=$SECRET_KEY
ExecStart=$VENV_DIR/bin/python3 $SCRIPT_DIR/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

    sudo systemctl daemon-reload
    sudo systemctl enable pictureviewer
    sudo systemctl start pictureviewer
    echo "Service installed and started."
    echo "  Status:  sudo systemctl status pictureviewer"
    echo "  Stop:    sudo systemctl stop pictureviewer"
    echo "  Restart: sudo systemctl restart pictureviewer"
fi

# Optional: firewall
echo ""
read -p "Open port $PORT in firewall (ufw)? [y/N]: " OPEN_FW
if [[ "$OPEN_FW" =~ ^[Yy]$ ]]; then
    if command -v ufw &> /dev/null; then
        sudo ufw allow "$PORT"/tcp
        echo "Firewall rule added."
    else
        echo "ufw not found. Manually open port $PORT if needed."
    fi
fi

# Get IP
IP_ADDRESS=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "unknown")

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
echo "    ./start_server.sh"
echo ""
echo "  Enter the URL and access code in the iOS app to connect."
echo ""
echo "  TIPS:"
echo "   - Phone and computer must be on the same network"
echo "   - Make sure port $PORT is not blocked by firewall"
echo "============================================================"
echo ""

# Start now?
read -p "Start the server now? [Y/n]: " START_NOW
if [[ ! "$START_NOW" =~ ^[Nn]$ ]]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
    "$VENV_DIR/bin/python3" "$SCRIPT_DIR/server.py"
fi

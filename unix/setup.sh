#!/usr/bin/env bash
# Lumina Gallery Server - macOS / Linux installer and updater.
#
# Started by install_mac.command (macOS) or install_linux.sh (Linux).
# Re-running it updates the server and keeps your settings.
#
#   1. copies the server to a per-user folder
#        macOS: ~/Library/Application Support/LuminaServer
#        Linux: ~/.local/share/lumina-server
#   2. finds a real Python 3.10 - 3.14 (or tells you how to install one)
#   3. builds a private virtual environment (pip --only-binary, checked)
#   4. keeps / migrates settings (config.json, or an old install's .env)
#   5-7. photo folder (safety-checked), access code, ports
#   8. firewall note (optional sudo step on Linux/macOS)
#   9. per-user auto-start: LaunchAgent (macOS) or systemd --user (Linux)
#  10. control commands: start / stop / status / add-folder / uninstall
#  11. starts the server and checks it answers
# No sudo is needed except for the optional firewall step.
#
# Optional environment switches (headless/SSH installs):
#   LUMINA_NO_GUI=1        never open the macOS folder picker; type the path
#   LUMINA_NO_AUTOSTART=1  don't install a LaunchAgent / systemd user service

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

GENERATED_CODE=""
BASE_PY=""

die() {
    bad "$1"
    echo "  Setup did not finish. Fix the problem above and run the installer again."
    exit 1
}

APP_FILES="server.py config.py safety.py lumina_admin.py add_folder.py run.py requirements.txt requirements-heic.txt README.md VERSION"

install_app_files() {
    step "Step 1/11: Copying the server program"
    if [ "$SOURCE_DIR" = "$APP_DIR" ]; then
        ok "Running from the installed copy - files already in place."
        return
    fi
    local f
    for f in $APP_FILES; do
        [ -f "$SOURCE_DIR/$f" ] || die "'$f' is missing next to the installer. Extract the WHOLE download and run it again."
    done
    mkdir -p "$LUMINA_BASE" "$LOG_DIR" "$CONFIG_DIR" || die "Cannot create $LUMINA_BASE"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
    local staging="$LUMINA_BASE/app.new"
    rm -rf "$staging"
    mkdir -p "$staging/unix"
    for f in $APP_FILES; do cp "$SOURCE_DIR/$f" "$staging/$f"; done
    cp "$SOURCE_DIR/unix/"*.sh "$staging/unix/"
    chmod +x "$staging/unix/"*.sh
    rm -rf "$APP_DIR"
    mv "$staging" "$APP_DIR"
    if [ "$LUMINA_OS" = mac ]; then
        xattr -dr com.apple.quarantine "$APP_DIR" 2>/dev/null || true
    fi
    ok "Installed to $APP_DIR"
}

python_ok() {   # python_ok <exe> -> prints version if usable (3.10-3.14, 64-bit)
    local out
    out="$("$1" "$ADMIN" env-info 2>/dev/null | sed -n 's/^@@JSON //p' | tail -n 1)" || return 1
    [ -n "$out" ] || return 1
    printf '%s' "$out" | JSON_PY="$1" jget supported | grep -q true || {
        note "Skipping $1 (Python $(printf '%s' "$out" | JSON_PY="$1" jget version); need 3.10 - 3.14)" >&2
        return 1
    }
    [ "$(printf '%s' "$out" | JSON_PY="$1" jget bits)" = "64" ] || { note "Skipping 32-bit $1" >&2; return 1; }
    printf '%s' "$out" | JSON_PY="$1" jget version
}

find_python() {
    step "Step 2/11: Finding Python"
    local cands=() v p
    for v in 3.13 3.12 3.14 3.11 3.10; do
        p="$(command -v "python$v" 2>/dev/null)" && cands+=("$p")
        if [ "$LUMINA_OS" = mac ]; then
            cands+=("/opt/homebrew/bin/python$v" "/usr/local/bin/python$v"
                    "/Library/Frameworks/Python.framework/Versions/$v/bin/python$v")
        fi
    done
    p="$(command -v python3 2>/dev/null)" && cands+=("$p")
    for p in ${cands[@]+"${cands[@]}"}; do
        [ -x "$p" ] || continue
        # macOS /usr/bin/python3 is the old Command Line Tools 3.9 (or a stub that
        # opens an installer dialog) - never use it.
        if [ "$LUMINA_OS" = mac ] && [ "$p" = "/usr/bin/python3" ]; then continue; fi
        if v="$(python_ok "$p")"; then
            BASE_PY="$p"
            ok "Using Python $v - $p"
            return 0
        fi
    done
    bad "No Python 3.10 - 3.14 was found."
    if [ "$LUMINA_OS" = mac ]; then
        echo "  Install Python 3.13, then run this installer again:"
        echo "    - easiest: download the macOS installer from https://www.python.org/downloads/macos/"
        echo "    - or, if you use Homebrew:  brew install python@3.13"
        command -v open >/dev/null && open "https://www.python.org/downloads/macos/" 2>/dev/null
    else
        local id=""
        [ -r /etc/os-release ] && id="$(. /etc/os-release; echo "${ID:-} ${ID_LIKE:-}")"
        echo "  Install Python 3.10 or newer with your package manager, then run this again:"
        case "$id" in
            *ubuntu*|*debian*) echo "    sudo apt update && sudo apt install python3 python3-venv" ;;
            *fedora*|*rhel*|*centos*) echo "    sudo dnf install python3" ;;
            *arch*) echo "    sudo pacman -S python" ;;
            *suse*) echo "    sudo zypper install python311" ;;
            *) echo "    (use your distribution's package manager to install python3 >= 3.10 with venv)" ;;
        esac
    fi
    exit 1
}

setup_venv() {
    step "Step 3/11: Preparing the Python environment"
    if [ -x "$VPY" ] && python_ok "$VPY" >/dev/null 2>&1; then
        ok "Reusing the existing environment."
    else
        [ -d "$VENV_DIR" ] && { note "Existing environment is broken or outdated - recreating it."; rm -rf "$VENV_DIR"; }
        local out
        if ! out="$("$BASE_PY" -m venv "$VENV_DIR" 2>&1)" || [ ! -x "$VPY" ]; then
            printf '%s\n' "$out" | tail -n 10 | sed 's/^/    /'
            if [ "$LUMINA_OS" = linux ]; then
                echo "  On Debian/Ubuntu the venv module is a separate package:"
                echo "    sudo apt install python3-venv     (or python3.X-venv for your version)"
            fi
            die "Could not create the Python environment."
        fi
        ok "Created $VENV_DIR"
    fi
    echo "  Installing packages (this can take a minute)..."
    "$VPY" -m pip install --quiet --upgrade --only-binary=:all: pip >/dev/null 2>&1 || true
    local log="$LUMINA_BASE/pip-install.log"
    if ! "$VPY" -m pip install --only-binary=:all: -r "$APP_DIR/requirements.txt" >"$log" 2>&1; then
        tail -n 25 "$log" | sed 's/^/    /'
        die "Installing the required packages failed (see above / $log). Check your internet connection."
    fi
    ok "Required packages installed."
    if ! "$VPY" -m pip install --only-binary=:all: -r "$APP_DIR/requirements-heic.txt" >>"$log" 2>&1; then
        note "Optional HEIC support (pillow-heif) could not be installed - the server works without it."
    fi
    local t
    t="$(printf '{}' | admin selftest)"
    [ "$(printf '%s' "$t" | jget ok)" = "true" ] || die "Self-test failed: $(printf '%s' "$t" | jget reason)"
    ok "Self-test passed (Pillow $(printf '%s' "$t" | jget pillow); HEIC: $(printf '%s' "$t" | jget heif); ffmpeg: $(printf '%s' "$t" | jget ffmpeg))."
    if [ "$(printf '%s' "$t" | jget ffmpeg)" != "true" ]; then
        if [ "$LUMINA_OS" = mac ]; then echo "  Tip: 'brew install ffmpeg' adds video thumbnails."
        else echo "  Tip: install ffmpeg with your package manager for video thumbnails."; fi
    fi
}

stop_old_services() {
    if [ "$LUMINA_OS" = mac ]; then
        local old="$HOME/Library/LaunchAgents/com.pictureviewer.server.plist"
        if [ -f "$old" ]; then
            note "Found the OLD login item 'com.pictureviewer.server' (previous version)."
            if ask_yn "Remove it so old and new servers don't fight over the ports? (it also contained your code in plain text)" Y; then
                launchctl bootout "gui/$(id -u)/com.pictureviewer.server" 2>/dev/null \
                    || launchctl unload "$old" 2>/dev/null || true
                rm -f "$old" && ok "Old login item removed."
            fi
        fi
    else
        if [ -f /etc/systemd/system/pictureviewer.service ]; then
            note "Found the OLD system service 'pictureviewer' (previous version, runs as root-managed service)."
            echo "  It would block the ports. Remove it with:"
            echo "    sudo systemctl disable --now pictureviewer && sudo rm /etc/systemd/system/pictureviewer.service && sudo systemctl daemon-reload"
            if ask_yn "Run that now (asks for your sudo password)?" N; then
                sudo systemctl disable --now pictureviewer \
                    && sudo rm -f /etc/systemd/system/pictureviewer.service \
                    && sudo systemctl daemon-reload && ok "Old service removed."
            fi
        fi
    fi
}

migrate_settings() {
    local dirs=() p r
    [ -f "$SOURCE_DIR/.env" ] && dirs+=("$SOURCE_DIR")
    if [ "${#dirs[@]}" -eq 0 ]; then
        echo
        echo "  If you used an older version of this server, its settings are in a file"
        echo "  called '.env' in the old server folder. Point me to that folder to keep"
        echo "  your access code and certificate (so phones stay connected)."
        read -r -p "  Old server folder (or press Enter to skip): " p || p=""
        p="${p/#\~/$HOME}"
        [ -n "$p" ] && dirs+=("$p")
    fi
    for p in ${dirs[@]+"${dirs[@]}"}; do
        r="$(printf '%s\0%s' "$p" "$CERT_DIR" | nul_json env_path cert_dir | admin migrate-env)"
        if [ "$(printf '%s' "$r" | jget ok)" = "true" ]; then
            ok "Imported settings from $(printf '%s' "$r" | jget old_dir)."
            echo "  The old folder was not changed; you can delete it once everything works."
            return 0
        fi
        note "Could not import from $p: $(printf '%s' "$r" | jget reason)"
    done
    return 1
}

choose_folder() {   # prints the chosen path
    local def="$1" p=""
    if [ "$LUMINA_OS" = mac ] && [ "${LUMINA_NO_GUI:-0}" != 1 ] && command -v osascript >/dev/null 2>&1; then
        echo "  A folder picker opened (it may be behind this window)." >&2
        p="$(osascript -e 'POSIX path of (choose folder with prompt "Choose the folder with your photos and videos (phones back up here)")' 2>/dev/null)" || p=""
        p="${p%/}"
    fi
    if [ -z "$p" ]; then
        read -r -p "  Photo folder path [$def]: " p || p=""
        p="${p/#\~/$HOME}"
        [ -z "$p" ] && p="$def"
    fi
    printf '%s' "$p"
}

ask_folder() {
    step "Step 5/11: Photo folder"
    echo "  Choose a folder that holds ONLY your photos/videos (for example ~/Pictures/Lumina"
    echo "  or a folder on an external drive). Not a whole drive, not your home folder."
    local def="$1" p r
    while :; do
        p="$(choose_folder "$def")"
        r="$(printf '%s' "$p" | nul_json path | admin validate-folder)"
        if [ "$(printf '%s' "$r" | jget ok)" != "true" ]; then
            bad "That folder can't be used: $(printf '%s' "$r" | jget reason)"
            continue
        fi
        p="$(printf '%s' "$r" | jget path)"
        if [ "$(printf '%s' "$r" | jget exists)" != "true" ]; then
            if ask_yn "'$p' does not exist. Create it?" Y; then mkdir -p "$p" || continue; else continue; fi
        fi
        if [ "$(printf '%s' "$r" | jget cloud_synced)" = "true" ]; then
            note "This folder is synced by iCloud/OneDrive; 'online-only' files are skipped by auto-organize."
            ask_yn "Use it anyway?" N || continue
        fi
        if [ "$LUMINA_OS" = mac ]; then
            case "$p" in
                "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*|/Volumes/*)
                    note "macOS privacy protection may stop the background server from reading this"
                    note "location. If the app shows the folder as not accessible, choose another folder"
                    note "(e.g. ~/Pictures/Lumina) or allow Python in System Settings > Privacy & Security."
                    ;;
            esac
        fi
        FOLDER="$p"
        ok "Photo folder: $p"
        return
    done
}

ask_code() {
    step "Step 6/11: Access code (the password phones use to connect)"
    echo "  1) Generate a strong code for me (recommended)"
    echo "  2) Type my own (12+ characters with lower case, UPPER case, a number and a symbol)"
    local c a b r
    while :; do
        read -r -p "  Choose 1 or 2 [1]: " c || c=""
        if [ -z "$c" ] || [ "$c" = "1" ]; then
            GENERATED_CODE="$(printf '{}' | admin gen-code | jget code)"
            CODE="$GENERATED_CODE"
            echo
            printf '  %sYour access code:  %s%s\n' "$C_GREEN" "$CODE" "$C_OFF"
            echo "  Write it down - you type it into the app on each phone."
            echo "  (Show it again later with: lumina status)"
            return
        fi
        if [ "$c" = "2" ]; then
            while :; do
                read -r -s -p "  Access code: " a || a=""; echo
                r="$(printf '%s' "$a" | nul_json code | admin validate-code)"
                if [ "$(printf '%s' "$r" | jget ok)" != "true" ]; then
                    bad "The code must contain $(printf '%s' "$r" | jget reason)."; continue
                fi
                read -r -s -p "  Type it again: " b || b=""; echo
                [ "$a" = "$b" ] || { bad "The two entries do not match."; continue; }
                CODE="$a"
                ok "Access code accepted."
                return
            done
        fi
    done
}

port_in_use() {
    "$VPY" -c '
import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
' "$1"
}

ask_port() {   # ask_port <label> <default> <other>  -> prints port
    local label="$1" def="$2" other="$3" p
    while :; do
        read -r -p "  $label port [$def]: " p || p=""
        [ -z "$p" ] && p="$def"
        case "$p" in *[!0-9]*|"") echo "  Enter a number from 1024 to 65535." >&2; continue ;; esac
        if [ "$p" -lt 1024 ] || [ "$p" -gt 65535 ]; then echo "  Enter a number from 1024 to 65535." >&2; continue; fi
        [ "$p" = "$other" ] && { echo "  HTTP and HTTPS need different ports." >&2; continue; }
        if port_in_use "$p"; then
            echo "  Port $p is already in use by another program. Close it or pick another port." >&2
            continue
        fi
        printf '%s' "$p"; return
    done
}

firewall_step() {
    step "Step 8/11: Firewall"
    if [ "$LUMINA_OS" = mac ]; then
        local fw=/usr/libexec/ApplicationFirewall/socketfilterfw state
        state="$("$fw" --getglobalstate 2>/dev/null || true)"
        case "$state" in
            *enabled*|*"State = 1"*|*"State = 2"*)
                local real
                real="$(printf '{}' | admin env-info | jget base_executable)"
                echo "  The macOS firewall is on. The first time a phone connects, macOS may ask"
                echo "  whether Python may accept incoming connections - click 'Allow'."
                echo "  Python: $real"
                if ask_yn "Allow it in the firewall now instead (asks for your password once)?" N; then
                    sudo "$fw" --add "$real" >/dev/null && sudo "$fw" --unblockapp "$real" >/dev/null \
                        && ok "Python allowed in the macOS firewall." || note "Could not change the firewall; allow it when macOS asks."
                fi
                ;;
            *) ok "The macOS firewall is off - nothing to do." ;;
        esac
        echo "  Only use this server on your home network (LAN). Never forward its ports on your router."
        return
    fi
    local subnet
    subnet="$("$VPY" -c '
import ipaddress, subprocess
try:
    out = subprocess.run(["ip", "-o", "-4", "route", "show", "default"], capture_output=True, text=True).stdout.split()
    dev = out[out.index("dev") + 1]
    addr = subprocess.run(["ip", "-o", "-4", "addr", "show", "dev", dev], capture_output=True, text=True).stdout.split()
    print(ipaddress.ip_interface(addr[addr.index("inet") + 1]).network)
except Exception:
    print("192.168.0.0/16")
' 2>/dev/null)"
    echo "  Phones must be able to reach ports $HTTP_PORT and $HTTPS_PORT on this computer, from your LAN only."
    if command -v ufw >/dev/null 2>&1; then
        echo "  If ufw is active, allow your LAN ($subnet) with:"
        echo "    sudo ufw allow from $subnet to any port $HTTP_PORT,$HTTPS_PORT proto tcp"
        if ask_yn "Run that now (asks for your sudo password)?" N; then
            sudo ufw allow from "$subnet" to any port "$HTTP_PORT,$HTTPS_PORT" proto tcp && ok "ufw rule added."
        fi
    elif command -v firewall-cmd >/dev/null 2>&1; then
        echo "  firewalld is installed. To allow your LAN ($subnet):"
        echo "    sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=$subnet port port=$HTTP_PORT protocol=tcp accept'"
        echo "    sudo firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=$subnet port port=$HTTPS_PORT protocol=tcp accept'"
        echo "    sudo firewall-cmd --reload"
    else
        ok "No ufw/firewalld found - nothing to do."
    fi
    echo "  Never forward these ports on your router."
}

autostart_step() {
    step "Step 9/11: Start automatically when you log in"
    if [ "${LUMINA_NO_AUTOSTART:-0}" = 1 ]; then
        note "Skipped (LUMINA_NO_AUTOSTART=1)."
        return 1
    fi
    if [ "$LUMINA_OS" = mac ]; then
        mkdir -p "$HOME/Library/LaunchAgents"
        # plistlib does the XML escaping; paths go in as argv (installer-owned, no secrets).
        "$VPY" - "$MAC_PLIST" "$MAC_LABEL" "$VPY" "$RUN_PY" "$APP_DIR" "$CONFIG_FILE" "$LOG_DIR" <<'PYEOF' || { bad "Could not write the LaunchAgent."; return 1; }
import os, plistlib, sys
plist, label, py, run, app, cfg, logs = sys.argv[1:8]
data = {
    "Label": label,
    "ProgramArguments": [py, run],
    "WorkingDirectory": app,
    "EnvironmentVariables": {"PICTUREVIEWER_CONFIG": cfg},
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "ThrottleInterval": 30,
    "ProcessType": "Background",
    "StandardOutPath": os.path.join(logs, "launchd.log"),
    "StandardErrorPath": os.path.join(logs, "launchd.log"),
}
tmp = plist + ".tmp"
with open(tmp, "wb") as fh:
    plistlib.dump(data, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, plist)
PYEOF
        launchctl bootout "gui/$(id -u)/$MAC_LABEL" 2>/dev/null || true
        ok "LaunchAgent $MAC_PLIST installed (starts at login, restarts if it crashes)."
        return 0
    fi
    if ! have_systemd_user; then
        note "systemd user services are not available here. Start the server with: lumina-server start"
        note "(add that command to your desktop's 'startup applications' to start it at login)."
        return 1
    fi
    mkdir -p "$(dirname "$UNIT_FILE")"
    "$VPY" - "$UNIT_FILE" "$VPY" "$RUN_PY" "$APP_DIR" "$CONFIG_FILE" <<'PYEOF' || { bad "Could not write the systemd unit."; return 1; }
import os, sys
unit, py, run, app, cfg = sys.argv[1:6]
def q(s):   # systemd quoting: escape \ and ", double % and $
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'
text = f"""[Unit]
Description=Lumina Gallery Server (photo backup for the Lumina Gallery app)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={q(app)}
Environment={q("PICTUREVIEWER_CONFIG=" + cfg)}
ExecStart={q(py)} {q(run)}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
tmp = unit + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(text)
os.replace(tmp, unit)
PYEOF
    systemctl --user daemon-reload && systemctl --user enable "$UNIT_NAME" >/dev/null 2>&1 \
        || { bad "Could not enable the user service."; return 1; }
    ok "systemd user service '$UNIT_NAME' enabled (starts when you log in)."
    if command -v loginctl >/dev/null 2>&1; then
        if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
            echo "  Tip: to keep it running after you log out / start it at boot without logging in:"
            echo "    sudo loginctl enable-linger $USER"
        fi
    fi
    return 0
}

commands_step() {
    step "Step 10/11: Control commands"
    local ctl="$APP_DIR/unix/lumina.sh"
    if [ "$LUMINA_OS" = mac ]; then
        mkdir -p "$MAC_SHORTCUT_DIR"
        local action name
        for action in start stop status add-folder uninstall; do
            case "$action" in
                start) name="Start" ;; stop) name="Stop" ;; status) name="Status" ;;
                add-folder) name="Add Folder" ;; uninstall) name="Uninstall" ;;
            esac
            {
                printf '#!/bin/bash\n'
                printf 'LUMINA_PAUSE=1 exec %q %q\n' "$ctl" "$action"
            } > "$MAC_SHORTCUT_DIR/Lumina Server - $name.command"
            chmod +x "$MAC_SHORTCUT_DIR/Lumina Server - $name.command"
        done
        ok "Double-click helpers are in ~/Applications/Lumina Server (Start/Stop/Status/Add Folder/Uninstall)."
        echo "  From Terminal you can also run: \"$ctl\" status"
    else
        mkdir -p "$(dirname "$LINUX_BIN_LINK")"
        ln -sf "$ctl" "$LINUX_BIN_LINK"
        ok "Command installed: lumina-server start|stop|status|add-folder|uninstall"
        case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) note "~/.local/bin is not on your PATH; run it as $LINUX_BIN_LINK" ;; esac
    fi
}

start_and_verify() {
    step "Step 11/11: Starting the server"
    service_start
    echo "  Waiting for the server to answer (up to 30 seconds)..."
    if wait_up "$HTTP_PORT" 30; then
        ok "The server is running."
        return 0
    fi
    bad "The server did not answer within 30 seconds."
    show_log_tail 30
    [ -f "$LOG_DIR/launchd.log" ] && tail -n 10 "$LOG_DIR/launchd.log" | sed 's/^/    /'
    return 1
}

show_summary() {
    local info ip
    info="$(printf '{}' | admin info)"
    echo
    printf '%s================================================================%s\n' "$C_GREEN" "$C_OFF"
    printf '%s  Lumina Gallery Server is installed%s\n' "$C_GREEN" "$C_OFF"
    printf '%s================================================================%s\n' "$C_GREEN" "$C_OFF"
    echo "  In the Lumina Gallery app, enter this server address:"
    local any=0
    while IFS= read -r ip; do
        [ -n "$ip" ] || continue
        any=1; printf '      %shttp://%s:%s%s\n' "$C_CYAN" "$ip" "$HTTP_PORT" "$C_OFF"
    done <<EOF2
$(printf '%s' "$info" | jget lan_ips)
EOF2
    [ "$any" = 1 ] || echo "      (no LAN address found - are you on Wi-Fi/Ethernet?)"
    if [ -n "$GENERATED_CODE" ]; then
        printf '  Access code: %s%s%s\n' "$C_GREEN" "$GENERATED_CODE" "$C_OFF"
    else
        echo "  Access code: the one you chose ('status' can show it)."
    fi
    local fp; fp="$(printf '%s' "$info" | jget fingerprint)"
    if [ -n "$fp" ]; then
        echo "  The app switches to HTTPS automatically. Certificate fingerprint (SHA-256):"
        echo "      $(printf '%s' "$fp" | sed 's/../&:/g; s/:$//' | tr '[:lower:]' '[:upper:]')"
    fi
    echo
    if [ "$LUMINA_OS" = mac ]; then echo "  Control: ~/Applications/Lumina Server (Start / Stop / Status / Add Folder / Uninstall)"
    else echo "  Control: lumina-server start | stop | status | add-folder | uninstall"; fi
    echo "  Logs: $LOG_FILE"
    echo "  To update later: download the new version and run the installer again (settings are kept)."
    printf '%s================================================================%s\n' "$C_GREEN" "$C_OFF"
}

main() {
    echo
    printf '%s================================================================%s\n' "$C_CYAN" "$C_OFF"
    printf '%s  Lumina Gallery Server - setup (%s)%s\n' "$C_CYAN" "$LUMINA_OS" "$C_OFF"
    printf '%s================================================================%s\n' "$C_CYAN" "$C_OFF"
    echo "  Program files: $LUMINA_BASE"

    if [ -x "$VPY" ] && [ -f "$RUN_PY" ]; then
        echo "  Stopping the running server (if any) for the update..."
        service_stop
    fi
    install_app_files
    ADMIN_PY="" JSON_PY=""
    find_python
    setup_venv
    stop_old_services

    step "Step 4/11: Settings"
    local cur keep=0
    cur="$(printf '{}' | admin read-config)"
    if [ "$(printf '%s' "$cur" | jget exists)" != "true" ]; then
        migrate_settings && cur="$(printf '{}' | admin read-config)"
    fi
    local cur_folder; cur_folder="$(printf '%s' "$cur" | jget media_folder)"
    if [ "$(printf '%s' "$cur" | jget exists)" = "true" ] && [ "$(printf '%s' "$cur" | jget has_code)" = "true" ] && [ -n "$cur_folder" ]; then
        echo "  Current settings:"
        echo "    Photo folder : $cur_folder"
        echo "    Ports        : HTTP $(printf '%s' "$cur" | jget port), HTTPS $(printf '%s' "$cur" | jget https_port)"
        echo "    Organize existing files: $(printf '%s' "$cur" | jget organize_existing)"
        local fc; fc="$(printf '%s' "$cur_folder" | nul_json path | admin validate-folder)"
        if [ "$(printf '%s' "$fc" | jget ok)" != "true" ]; then
            bad "The current photo folder can no longer be used: $(printf '%s' "$fc" | jget reason)"
        elif ask_yn "Keep current settings?" Y; then
            keep=1
        fi
    fi

    local payload r regen=0
    if [ "$keep" = 1 ]; then
        payload="$(printf '%s\0%s\0%s' "$LOG_DIR" "$CERT_DIR" "$THUMB_DIR" | nul_json log_dir cert_dir thumbnail_dir \
            | "$VPY" -c 'import json,sys; print(json.dumps({"values": json.load(sys.stdin)}))')"
        HTTP_PORT="$(printf '%s' "$cur" | jget port)"; HTTPS_PORT="$(printf '%s' "$cur" | jget https_port)"
        ok "Keeping your settings."
    else
        local def="${cur_folder:-$HOME/Pictures/Lumina}"
        FOLDER=""; ask_folder "$def"
        echo
        echo "  New uploads from your phone are always sorted into Photos/Year/Month and"
        echo "  Videos/Year/Month. The server can also tidy what is ALREADY in this folder."
        local org=1 orgdef=Y
        [ "$(printf '%s' "$cur" | jget organize_existing)" = "false" ] && orgdef=N
        ask_yn "Organize photos already in this folder into Photos/Videos/Year/Month? This MOVES files (originals kept, nothing deleted)" "$orgdef" || org=0
        CODE=""
        if [ "$(printf '%s' "$cur" | jget has_code)" = "true" ]; then
            ask_yn "Keep your current access code?" Y || ask_code
        else
            ask_code
        fi
        step "Step 7/11: Network ports"
        echo "  Press Enter to keep the defaults unless another program uses them."
        local dh dhs
        dh="$(printf '%s' "$cur" | jget port)"; dhs="$(printf '%s' "$cur" | jget https_port)"
        HTTP_PORT="$(ask_port HTTP "${dh:-8500}" -1)"
        HTTPS_PORT="$(ask_port HTTPS "${dhs:-8543}" "$HTTP_PORT")"
        if [ "$(printf '%s' "$cur" | jget has_secret)" = "true" ]; then
            ask_yn "Sign out all phones (they will need the access code again)?" N && regen=1
        fi
        # All values go to Python on stdin, NUL-separated; Python builds the JSON.
        payload="$(printf '%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s' "$FOLDER" "$CODE" "$HTTP_PORT" "$HTTPS_PORT" \
                "$org" "$regen" "$LOG_DIR" "$CERT_DIR" "$THUMB_DIR" | "$VPY" -c '
import json, sys
v = sys.stdin.buffer.read().decode("utf-8", "surrogateescape").split("\0")
vals = {"media_folder": v[0], "port": int(v[2]), "https_port": int(v[3]),
        "auto_organize": True, "organize_existing": v[4] == "1",
        "log_dir": v[6], "cert_dir": v[7], "thumbnail_dir": v[8]}
if v[1]:
    vals["access_code"] = v[1]
print(json.dumps({"values": vals, "regen_secret": v[5] == "1"}))
')"
    fi
    r="$(printf '%s' "$payload" | admin write-config)"
    payload=""
    [ "$(printf '%s' "$r" | jget ok)" = "true" ] || die "Could not save settings: $(printf '%s' "$r" | jget reason)"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
    ok "Settings saved to $CONFIG_FILE (readable by you only)."

    firewall_step
    autostart_step
    commands_step
    if start_and_verify; then show_summary; else exit 1; fi
}

main "$@"

#!/usr/bin/env bash
# Lumina Gallery Server control (macOS / Linux):
#   lumina.sh start | stop | restart | status | add-folder | uninstall
# On Linux the installer links this as ~/.local/bin/lumina-server; on macOS
# there are double-click helpers in ~/Applications/Lumina Server.
set -u
set -o pipefail
SELF="$0"
while [ -L "$SELF" ]; do
    t="$(readlink "$SELF")"
    case "$t" in /*) SELF="$t" ;; *) SELF="$(dirname "$SELF")/$t" ;; esac
done
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

finish() {
    if [ "${LUMINA_PAUSE:-0}" = 1 ]; then
        echo; read -r -p "  Press Enter to close this window " _ || true
    fi
    exit "${1:-0}"
}

require_install() {
    [ -x "$VPY" ] && [ -f "$RUN_PY" ] || { bad "The server is not installed. Run the installer."; finish 1; }
}

cmd_start() {
    require_install
    local port; port="$(configured_port)"
    if server_up "$port"; then ok "The server is already running (port $port)."; return 0; fi
    service_start
    echo "  Starting... (up to 30 seconds)"
    if wait_up "$port" 30; then ok "The server is running."; return 0; fi
    bad "The server did not start."
    show_log_tail 20
    return 1
}

cmd_stop() {
    service_stop
    ok "The server is stopped. It starts again automatically the next time you log in."
}

cmd_status() {
    require_install
    local info port https ip fp r
    info="$(printf '{}' | admin info)"
    port="$(printf '%s' "$info" | jget port)"; https="$(printf '%s' "$info" | jget https_port)"
    echo
    printf '%s  Lumina Gallery Server - status%s\n' "$C_CYAN" "$C_OFF"
    if server_up "$port"; then ok "Running (HTTP port $port, HTTPS port $https)"
    else bad "Not answering on port $port. Start it with the Start helper / 'lumina-server start'."; fi
    if [ "$LUMINA_OS" = mac ]; then
        if launchctl print "gui/$(id -u)/$MAC_LABEL" >/dev/null 2>&1; then echo "  Login item: loaded"
        elif [ -f "$MAC_PLIST" ]; then echo "  Login item: installed (not loaded right now)"
        else echo "  Login item: not installed"; fi
    elif [ -f "$UNIT_FILE" ] && have_systemd_user; then
        echo "  User service: $(systemctl --user is-enabled "$UNIT_NAME" 2>/dev/null), $(systemctl --user is-active "$UNIT_NAME" 2>/dev/null)"
    fi
    echo
    echo "  Server address for the app:"
    while IFS= read -r ip; do
        [ -n "$ip" ] && printf '      %shttp://%s:%s%s\n' "$C_CYAN" "$ip" "$port" "$C_OFF"
    done <<EOF2
$(printf '%s' "$info" | jget lan_ips)
EOF2
    echo
    echo "  Shared folders:"
    printf '%s' "$info" | "$VPY" -c '
import json, sys
for r in json.load(sys.stdin).get("roots", []):
    print("      %s: %s  [%s]" % (r["name"], r["path"], "ok" if r["accessible"] else "NOT ACCESSIBLE"))'
    echo "  Auto-organize uploads: $(printf '%s' "$info" | jget auto_organize); existing files: $(printf '%s' "$info" | jget organize_existing)"
    echo
    fp="$(printf '%s' "$info" | jget fingerprint)"
    echo "  Certificate fingerprint (SHA-256) - the app pins this:"
    if [ -n "$fp" ]; then echo "      $(printf '%s' "$fp" | sed 's/../&:/g; s/:$//' | tr '[:lower:]' '[:upper:]')"
    else echo "      (no certificate yet - it is created on first start)"; fi
    echo
    show_log_tail 20
    echo
    if ask_yn "Show the access code on screen?" N; then
        r="$(printf '{}' | admin show-code | jget code)"
        printf '  Access code: %s%s%s\n' "$C_GREEN" "$r" "$C_OFF"
    fi
}

cmd_add_folder() {
    require_install
    local name path r
    echo "  Add a backup folder (each phone can pick its own folder in the app)."
    read -r -p "  Name to show in the app (e.g. Wife's iPhone): " name || name=""
    path=""
    if [ "$LUMINA_OS" = mac ] && [ "${LUMINA_NO_GUI:-0}" != 1 ] && command -v osascript >/dev/null 2>&1; then
        path="$(osascript -e 'POSIX path of (choose folder with prompt "Choose the backup folder")' 2>/dev/null)" || path=""
        path="${path%/}"
    fi
    if [ -z "$path" ]; then
        read -r -p "  Full folder path: " path || path=""
        path="${path/#\~/$HOME}"
    fi
    r="$(printf '%s\0%s' "$name" "$path" | nul_json name path | admin add-folder)"
    if [ "$(printf '%s' "$r" | jget ok)" = "true" ]; then
        ok "$(printf '%s' "$r" | jget message)"
        echo "  It appears in the app shortly (no restart needed)."
    else
        bad "$(printf '%s' "$r" | jget reason)"
        return 1
    fi
}

cmd_uninstall() {
    echo
    echo "  This removes the Lumina Gallery Server from this computer."
    echo "  Your photos and videos are NOT deleted."
    ask_yn "Uninstall now?" N || { echo "  Nothing was changed."; return 0; }
    service_stop
    if [ "$LUMINA_OS" = mac ]; then
        rm -f "$MAC_PLIST"
        rm -rf "$MAC_SHORTCUT_DIR"
    else
        if [ -f "$UNIT_FILE" ]; then
            have_systemd_user && systemctl --user disable "$UNIT_NAME" >/dev/null 2>&1
            rm -f "$UNIT_FILE"
            have_systemd_user && systemctl --user daemon-reload
        fi
        [ -L "$LINUX_BIN_LINK" ] && rm -f "$LINUX_BIN_LINK"
    fi
    ok "Auto-start and shortcuts removed."
    case "$LUMINA_BASE" in
        "$HOME"/*) ;;
        *) bad "Unexpected install folder $LUMINA_BASE - not deleting anything."; return 1 ;;
    esac
    rm -rf "$VENV_DIR" "$LUMINA_BASE/cache" "$APP_DIR" "$LUMINA_BASE/app.new" "$LUMINA_BASE/pip-install.log" "$PID_FILE"
    ok "Program files removed."
    echo
    echo "  Your settings (access code, certificate, extra folder list) are in:"
    echo "    $CONFIG_DIR"
    echo "  Keep them if you plan to reinstall - phones then stay connected."
    if ask_yn "Delete the settings and logs too?" N; then
        rm -rf "$CONFIG_DIR" "$LOG_DIR"
        rmdir "$LUMINA_BASE" 2>/dev/null || true
        ok "Settings and logs deleted."
    fi
    echo "  If you added a firewall rule (ufw / macOS firewall), you can remove it the same way."
    ok "Uninstalled. Your photo folders were not touched."
}

case "${1:-status}" in
    start) cmd_start; finish $? ;;
    stop) cmd_stop; finish $? ;;
    restart) service_stop; cmd_start; finish $? ;;
    status) cmd_status; finish $? ;;
    add-folder) cmd_add_folder; finish $? ;;
    uninstall) cmd_uninstall; finish $? ;;
    *) echo "Usage: $(basename "$0") start|stop|restart|status|add-folder|uninstall"; finish 2 ;;
esac

#!/usr/bin/env bash
# Shared paths + helpers for the macOS/Linux installer and the `lumina`
# control script. Sourced, never executed directly.
#
# User values (folders, access codes) are never put on a command line: they
# are written to Python's STDIN (NUL-separated or JSON) and Python writes
# config.json with the json module.

case "$(uname -s)" in
    Darwin)
        LUMINA_OS=mac
        LUMINA_BASE="$HOME/Library/Application Support/LuminaServer"
        ;;
    *)
        LUMINA_OS=linux
        LUMINA_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/lumina-server"
        ;;
esac

APP_DIR="$LUMINA_BASE/app"
VENV_DIR="$LUMINA_BASE/venv"
CONFIG_DIR="$LUMINA_BASE/config"
CONFIG_FILE="$CONFIG_DIR/config.json"
CERT_DIR="$CONFIG_DIR/certs"
LOG_DIR="$LUMINA_BASE/logs"
THUMB_DIR="$LUMINA_BASE/cache/thumbnails"
LOG_FILE="$LOG_DIR/server.log"
VPY="$VENV_DIR/bin/python"
ADMIN="$APP_DIR/lumina_admin.py"
RUN_PY="$APP_DIR/run.py"
PID_FILE="$LUMINA_BASE/server.pid"

MAC_LABEL="com.lumina.server"
MAC_PLIST="$HOME/Library/LaunchAgents/$MAC_LABEL.plist"
MAC_SHORTCUT_DIR="$HOME/Applications/Lumina Server"
UNIT_NAME="lumina-server.service"
UNIT_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT_NAME"
LINUX_BIN_LINK="$HOME/.local/bin/lumina-server"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PIP_DISABLE_PIP_VERSION_CHECK=1

if [ -t 1 ]; then
    C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_OFF=$'\033[0m'
else
    C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_OFF=""
fi
step() { printf '\n%s== %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '  %sOK%s   %s\n' "$C_GREEN" "$C_OFF" "$1"; }
note() { printf '  %sNOTE%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
bad()  { printf '  %sERROR%s %s\n' "$C_RED" "$C_OFF" "$1"; }

# ask_yn "Question" Y|N  -> exit status 0 for yes
ask_yn() {
    local q="$1" def="${2:-Y}" a suffix="[Y/n]"
    [ "$def" = "N" ] && suffix="[y/N]"
    while :; do
        read -r -p "  $q $suffix " a || a=""
        a="$(printf '%s' "$a" | tr '[:upper:]' '[:lower:]')"
        if [ -z "$a" ]; then [ "$def" = "Y" ]; return; fi
        case "$a" in y|yes) return 0 ;; n|no) return 1 ;; esac
        echo "  Please answer y or n."
    done
}

# admin <command>   (JSON payload on stdin) -> prints the result JSON object
# Uses $ADMIN_PY if set (e.g. the base Python before the venv exists).
admin() {
    local py="${ADMIN_PY:-$VPY}"
    PICTUREVIEWER_CONFIG="$CONFIG_FILE" "$py" "$ADMIN" "$1" | sed -n 's/^@@JSON //p' | tail -n 1
}

# jget <key>   (JSON on stdin) -> value; booleans as true/false, lists one per line
jget() {
    "${JSON_PY:-$VPY}" -c '
import json, sys
raw = sys.stdin.read().strip()
d = json.loads(raw) if raw else {}
v = d.get(sys.argv[1]) if isinstance(d, dict) else None
if v is None:
    print("")
elif isinstance(v, bool):
    print("true" if v else "false")
elif isinstance(v, list):
    print("\n".join(str(x) for x in v))
else:
    print(v)
' "$1"
}

# nul_json key1 key2 ...  (NUL-separated values on stdin) -> {"key1": "v1", ...}
nul_json() {
    "${JSON_PY:-$VPY}" -c '
import json, sys
vals = sys.stdin.buffer.read().decode("utf-8", "surrogateescape").split("\0")
print(json.dumps(dict(zip(sys.argv[1:], vals))))
' "$@"
}

configured_port() {
    local p=""
    if [ -f "$CONFIG_FILE" ] && [ -x "$VPY" ]; then
        p="$(printf '{}' | admin read-config | jget port)"
    fi
    printf '%s' "${p:-8500}"
}

server_up() {   # server_up <port>
    "${JSON_PY:-$VPY}" -c '
import sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/status" % sys.argv[1], timeout=2) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
' "$1"
}

wait_up() {     # wait_up <port> <seconds>
    local i=0
    while [ "$i" -lt "${2:-30}" ]; do
        server_up "$1" && return 0
        sleep 1; i=$((i + 1))
    done
    return 1
}

show_log_tail() {
    local n="${1:-20}"
    if [ -f "$LOG_FILE" ]; then
        echo "  Last $n log lines ($LOG_FILE):"
        tail -n "$n" "$LOG_FILE" | sed 's/^/    /'
    else
        echo "  No log file yet at $LOG_FILE"
    fi
}

have_systemd_user() {
    command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1
}

# Start/stop the per-user service (LaunchAgent / systemd --user / plain process).
service_start() {
    if [ "$LUMINA_OS" = mac ] && [ -f "$MAC_PLIST" ]; then
        launchctl bootstrap "gui/$(id -u)" "$MAC_PLIST" 2>/dev/null || true
        launchctl kickstart "gui/$(id -u)/$MAC_LABEL" 2>/dev/null || true
        return 0
    fi
    if [ "$LUMINA_OS" = linux ] && [ -f "$UNIT_FILE" ] && have_systemd_user; then
        systemctl --user start "$UNIT_NAME"
        return $?
    fi
    # No service manager: background process with a pid file.
    mkdir -p "$LOG_DIR"
    PICTUREVIEWER_CONFIG="$CONFIG_FILE" nohup "$VPY" "$RUN_PY" >/dev/null 2>&1 &
    echo $! > "$PID_FILE"
}

service_stop() {
    if [ "$LUMINA_OS" = mac ]; then
        launchctl bootout "gui/$(id -u)/$MAC_LABEL" 2>/dev/null || true
    elif [ -f "$UNIT_FILE" ] && have_systemd_user; then
        systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
    fi
    if [ -f "$PID_FILE" ]; then
        local pid; pid="$(cat "$PID_FILE" 2>/dev/null)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            # only kill it if it really is our server
            if ps -p "$pid" -o command= 2>/dev/null | grep -F -q "$RUN_PY"; then
                kill "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$PID_FILE"
    fi
    # Anything else still running our run.py (e.g. started by hand).
    local pids
    pids="$(ps -axo pid=,command= 2>/dev/null | grep -F "$RUN_PY" | grep -v grep | awk '{print $1}')"
    [ -n "$pids" ] && kill $pids 2>/dev/null || true
    sleep 1
    return 0
}

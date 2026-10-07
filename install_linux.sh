#!/usr/bin/env bash
# Lumina Gallery Server - Linux installer.  Run:  bash install_linux.sh
# Installs per user (no sudo needed except for an optional firewall rule).
cd "$(dirname "$0")" || exit 1
exec bash "./unix/setup.sh" "$@"

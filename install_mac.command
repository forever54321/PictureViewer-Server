#!/bin/bash
# Lumina Gallery Server - macOS installer. Double-click this file.
# First time: if macOS says it "cannot be opened", right-click it > Open
# (macOS 15+: System Settings > Privacy & Security > "Open Anyway"),
# or run in Terminal:  bash install_mac.command
cd "$(dirname "$0")" || exit 1
bash "./unix/setup.sh" "$@"
status=$?
echo
read -r -p "Press Enter to close this window " _
exit $status

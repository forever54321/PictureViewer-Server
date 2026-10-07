#!/usr/bin/env bash
# The macOS installer is now install_mac.command (double-click it) /
# unix/setup.sh. This wrapper is kept so old instructions still work.
cd "$(dirname "$0")" || exit 1
exec bash "./unix/setup.sh" "$@"

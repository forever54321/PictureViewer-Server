#!/usr/bin/env bash
# Build dist/lumina-server-{windows,macos,linux}-<VERSION> archives + SHA256SUMS.
# Usage: packaging/build_release.sh      (needs python3 >= 3.8; no other tools)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="$(command -v python3 || command -v python)"
exec "$PY" packaging/build_release.py "$@"

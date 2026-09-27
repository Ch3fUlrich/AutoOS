#!/usr/bin/env sh
# Start (or reattach) a persistent Herdr agent-multiplexer session.
# Installs Herdr if it is missing, then launches it in the target directory.
#
# Usage: ./start-session.sh [work-dir]
#   work-dir  Directory to start the session in (default: current directory).
set -eu

WORK_DIR="${1:-$(pwd)}"

if ! command -v herdr >/dev/null 2>&1; then
  echo "herdr not found — installing via https://herdr.dev/install.sh ..."
  curl -fsSL https://herdr.dev/install.sh | sh
fi

if ! command -v herdr >/dev/null 2>&1; then
  echo "herdr install did not put 'herdr' on PATH. Open a new shell or add it, then re-run." >&2
  exit 1
fi

echo "Starting/reattaching Herdr in: $WORK_DIR"
echo "  detach with ctrl+b q — agents keep running; re-run this script to reattach."
cd "$WORK_DIR"
exec herdr

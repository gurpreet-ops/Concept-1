#!/usr/bin/env bash
# Start an MCP server from this repo (used by .mcp.json).
# Usage: scripts/run_server.sh google-ads|meta-ads
#
# On a fresh checkout (e.g. a cloud session on the Equal AI runner) this first creates
# .venv and installs the project. Everything except the server itself writes to stderr,
# because stdout is the MCP channel.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  # Both servers start at once; let one install while the other waits.
  until mkdir .venv.lock 2>/dev/null; do sleep 1; done
  trap 'rmdir .venv.lock' EXIT
  if [ ! -x .venv/bin/python ]; then
    PY=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
    echo "Installing ads-mcp into .venv with $PY ..." >&2
    "$PY" -m venv .venv >&2
    .venv/bin/pip install -q --disable-pip-version-check -e . >&2
  fi
  rmdir .venv.lock
  trap - EXIT
fi

exec .venv/bin/python scripts/run_server.py "$@"

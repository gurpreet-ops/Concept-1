#!/usr/bin/env bash
# Start meta-ads-mcp or google-ads-mcp in a Claude Code cloud session.
#
# Usage: scripts/cloud_mcp.sh meta-ads-mcp|google-ads-mcp
#
# Cloud sessions start from a fresh checkout, so the first call creates .venv and
# installs the package. Install output goes to stderr: stdout carries the MCP protocol.
# Credentials come from the cloud environment's variables (META_ACCESS_TOKEN, GOOGLE_ADS_*).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cmd="${1:?usage: cloud_mcp.sh meta-ads-mcp|google-ads-mcp}"
bin="$root/.venv/bin/$cmd"

install() {
  if [ ! -x "$bin" ]; then
    python3 -m venv "$root/.venv"
    "$root/.venv/bin/pip" install --quiet --disable-pip-version-check -e "$root"
  fi
}

if [ ! -x "$bin" ]; then
  # Both servers start at once; the lock stops them installing over each other.
  if command -v flock >/dev/null; then
    (flock 9; install) 9>"$root/.venv.lock" >&2
  else
    install >&2
  fi
fi

exec "$bin"

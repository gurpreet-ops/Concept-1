"""Run the google-ads or meta-ads MCP server, finding credentials wherever they are configured.

Credentials come from the environment (how the Equal AI cloud runner provides them). When a
variable is missing there, it is taken from the same server's entry in ~/.claude.json, which
is where scripts/setup_claude.py saves them on a Mac. So this one project config works both
in cloud sessions and in Claude Code on a Mac.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVERS = {
    "google-ads": ("ads_mcp.google.server", ["GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
                                             "GOOGLE_ADS_REFRESH_TOKEN"]),
    "meta-ads": ("ads_mcp.meta.server", ["META_ACCESS_TOKEN"]),
}


def fill_from_claude_config(name: str) -> None:
    config = Path.home() / ".claude.json"
    if not config.exists():
        return
    try:
        saved = json.loads(config.read_text()).get("mcpServers", {}).get(name, {}).get("env", {})
    except (ValueError, OSError):
        return
    for key, value in saved.items():
        os.environ.setdefault(key, value)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in SERVERS:
        sys.exit(f"Usage: run_server.py {'|'.join(SERVERS)}")
    name = sys.argv[1]
    module, required = SERVERS[name]
    if any(not os.environ.get(key) for key in required):
        fill_from_claude_config(name)
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        # Start anyway: each tool then explains which variable is missing.
        print(f"{name}: missing {', '.join(missing)}; tools will report this until it is set.", file=sys.stderr)
    __import__(module, fromlist=["main"]).main()


if __name__ == "__main__":
    main()

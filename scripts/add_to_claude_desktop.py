"""Copy the google-ads / meta-ads servers from Claude Code into the Claude desktop app.

Usage (after scripts/setup_claude.py has succeeded):
    .venv/bin/python scripts/add_to_claude_desktop.py

Reads the servers registered in Claude Code (~/.claude.json) and adds them to
the desktop app's claude_desktop_config.json, so nothing has to be re-entered.
Quit and reopen the Claude desktop app afterwards.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path

SERVERS = ("google-ads", "meta-ads")


def desktop_config_path() -> Path:
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system() == "Windows":
        return home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def main() -> None:
    code_config = Path.home() / ".claude.json"
    if not code_config.exists():
        sys.exit("Claude Code config (~/.claude.json) not found. Run scripts/setup_claude.py first.")
    registered = json.loads(code_config.read_text()).get("mcpServers", {})
    found = {name: registered[name] for name in SERVERS if name in registered}
    if not found:
        sys.exit("No google-ads or meta-ads server found in Claude Code. Run scripts/setup_claude.py first.")

    path = desktop_config_path()
    config = json.loads(path.read_text()) if path.exists() else {}
    if path.exists():
        shutil.copy(path, path.with_suffix(".json.bak"))
    servers = config.setdefault("mcpServers", {})
    for name, entry in found.items():
        servers[name] = {"command": entry["command"], "args": entry.get("args", []), "env": entry.get("env", {})}
        print(f"  ✓ {name} added to the Claude desktop app")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))
    print("\nDone. Quit the Claude desktop app completely (Cmd+Q) and open it again.")


if __name__ == "__main__":
    main()

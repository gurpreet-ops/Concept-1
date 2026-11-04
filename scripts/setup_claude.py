"""Interactive setup: asks for your credentials and registers the servers with Claude Code.

Usage (from the project folder, after `pip install -e .`):
    .venv/bin/python scripts/setup_claude.py

Secrets are typed into hidden prompts and passed straight to `claude mcp add`;
nothing is written to this repo.
"""

from __future__ import annotations

import asyncio
import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from google_ads_refresh_token import get_refresh_token

from ads_mcp.google.client import GoogleAdsClient

BIN = Path(__file__).resolve().parent.parent / ".venv" / "bin"


def ask(label: str, secret: bool = False, optional: bool = False, check=None) -> str:
    while True:
        prompt = f"{label}{' (press Enter to skip)' if optional else ''}: "
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if not value and optional:
            return ""
        if not value:
            print("  This one is required.")
            continue
        problem = check(value) if check else None
        if problem:
            print(f"  {problem}")
            continue
        return value


def register(name: str, env: dict[str, str], command: Path) -> None:
    subprocess.run(["claude", "mcp", "remove", "--scope", "user", name], capture_output=True)
    args = ["claude", "mcp", "add", "--scope", "user", name]
    for key, value in env.items():
        args += ["--env", f"{key}={value}"]
    args += ["--", str(command)]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  ✓ {name} added to Claude Code")
    else:
        print(f"  ✗ Could not add {name}: {result.stderr.strip() or result.stdout.strip()}")


def setup_google() -> None:
    print("\n=== Google Ads ===")
    env = {
        "GOOGLE_ADS_CLIENT_ID": ask(
            "Client ID (ends with .apps.googleusercontent.com)",
            check=lambda v: None if v.endswith(".apps.googleusercontent.com")
            else "That doesn't look like a client ID; it should end with .apps.googleusercontent.com",
        ),
        "GOOGLE_ADS_CLIENT_SECRET": ask(
            "Client secret (starts with GOCSPX-, typing is hidden)", secret=True,
            check=lambda v: None if v.startswith("GOCSPX-")
            else "The client secret should start with GOCSPX-",
        ),
    }
    if input("Do you already have a refresh token? [y/N]: ").strip().lower() == "y":
        env["GOOGLE_ADS_REFRESH_TOKEN"] = ask(
            "Refresh token (starts with 1//, typing is hidden)", secret=True,
            check=lambda v: "That's the access token (ya29.). Use the refresh token, starting with 1//"
            if v.startswith("ya29.") else None,
        )
    else:
        env["GOOGLE_ADS_REFRESH_TOKEN"] = get_refresh_token(
            env["GOOGLE_ADS_CLIENT_ID"], env["GOOGLE_ADS_CLIENT_SECRET"])
        print("  ✓ Got a refresh token from Google")
    manager = ask(
        "Manager (MCC) account ID, e.g. 123-456-7890", optional=True,
        check=lambda v: None if v.replace("-", "").isdigit() else "Use digits only, e.g. 123-456-7890",
    )
    if manager:
        env["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = manager.replace("-", "")
    register("google-ads", env, BIN / "google-ads-mcp")
    asyncio.run(check_google(env))


async def check_google(env: dict[str, str]) -> None:
    print("  Testing the credentials with Google...")
    client = GoogleAdsClient(
        developer_token=None,
        client_id=env["GOOGLE_ADS_CLIENT_ID"],
        client_secret=env["GOOGLE_ADS_CLIENT_SECRET"],
        refresh_token=env["GOOGLE_ADS_REFRESH_TOKEN"],
        login_customer_id=env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
    )
    try:
        customers = await client.list_accessible_customers()
        print(f"  ✓ Google Ads works. Accounts you can access: {', '.join(customers) or 'none'}")
    except Exception as e:  # report and carry on; the server is registered either way
        print(f"  ✗ Google rejected the credentials: {e}")
    finally:
        await client.aclose()


def setup_meta() -> None:
    print("\n=== Meta Ads ===")
    token = ask("Meta access token (typing is hidden)", secret=True, optional=True)
    if not token:
        print("  Skipped. Run this script again once you have the token.")
        return
    register("meta-ads", {"META_ACCESS_TOKEN": token}, BIN / "meta-ads-mcp")


def main() -> None:
    if not shutil.which("claude"):
        sys.exit("Claude Code ('claude' command) was not found. Install it first, then rerun this script.")
    for cmd in ("google-ads-mcp", "meta-ads-mcp"):
        if not (BIN / cmd).exists():
            sys.exit(f"{BIN / cmd} not found. Run `pip install -e .` inside the project's .venv first.")

    if input("Set up Google Ads? [Y/n]: ").strip().lower() != "n":
        setup_google()
    if input("Set up Meta Ads? [y/N]: ").strip().lower() == "y":
        setup_meta()
    print("\nDone. Restart Claude Code and ask: \"List my Google Ads accounts\".")


if __name__ == "__main__":
    main()

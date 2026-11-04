"""Get a Google Ads OAuth refresh token via the installed-app (loopback) flow.

Usage:
    GOOGLE_ADS_CLIENT_ID=... GOOGLE_ADS_CLIENT_SECRET=... python scripts/google_ads_refresh_token.py

Create a "Desktop app" OAuth client in Google Cloud Console first. A browser
window opens; sign in with a Google account that has access to your Ads
accounts, and the refresh token is printed.
"""

from __future__ import annotations

import http.server
import os
import secrets
import urllib.parse
import webbrowser

import httpx

PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{PORT}/"
SCOPE = "https://www.googleapis.com/auth/adwords"


def main() -> None:
    client_id = os.environ["GOOGLE_ADS_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_ADS_CLIENT_SECRET"]
    state = secrets.token_urlsafe(16)
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            result.update(params)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *args) -> None:
            pass

    print(f"Opening browser. If it does not open, visit:\n\n{auth_url}\n")
    webbrowser.open(auth_url)
    with http.server.HTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        while "code" not in result and "error" not in result:
            httpd.handle_request()

    if result.get("state") != state:
        raise SystemExit("State mismatch; aborting.")
    if "error" in result:
        raise SystemExit(f"Authorization failed: {result['error']}")

    resp = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": result["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    print("GOOGLE_ADS_REFRESH_TOKEN=" + resp.json()["refresh_token"])


if __name__ == "__main__":
    main()

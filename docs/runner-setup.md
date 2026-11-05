# Running the ads MCP servers on the Equal AI (B2C) runner

This repo's `.mcp.json` makes every Claude Code session on it start two read-only MCP
servers: `google-ads` and `meta-ads`. The first session on a fresh checkout installs them
into `.venv` automatically (about 15 seconds). The servers only need their credentials.

## For the runner admin

Add these as **secret environment variables** on the `Equal AI (B2C)` runner pool, so they
reach every session's environment:

| Variable | What it is |
|---|---|
| `GOOGLE_ADS_CLIENT_ID` | OAuth client ID (Desktop app type), ends in `.apps.googleusercontent.com` |
| `GOOGLE_ADS_CLIENT_SECRET` | That client's secret, starts with `GOCSPX-` |
| `GOOGLE_ADS_REFRESH_TOKEN` | Refresh token for the Google user with Ads access, starts with `1//` |
| `META_ACCESS_TOKEN` | Meta system-user token with `ads_read`, starts with `EAA` |

Optional:

| Variable | When |
|---|---|
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Only if accounts are reached through a manager (MCC) account; digits only |
| `META_APP_SECRET` | Signs Meta requests with `appsecret_proof` |
| `MCP_TIMEOUT=60000` | If the first session's install is slower than Claude Code's server start-up timeout |

The values are the ones already used on the Mac setup; the owner can share them with you
directly (not through chat). Both servers are read-only: they cannot change campaigns,
budgets or ads.

Requirements on the runner image: `python3` 3.10 or newer, `bash`, outbound HTTPS to
`graph.facebook.com`, `googleads.googleapis.com`, `oauth2.googleapis.com` and PyPI.

### Optional: video creative analysis

The `meta-ads` video review tools also need `ffmpeg`/`ffprobe`, `whisper-cli`
([whisper.cpp](https://github.com/ggml-org/whisper.cpp)) on the `PATH`, and a whisper model
file. Point `CREATIVE_DATA_DIR` at a persistent folder containing `models/ggml-small.bin`,
so tags and the model survive between sessions (session workspaces are temporary).

## Checking it works

In a new session on this repo, ask Claude: "List my Google Ads accounts" and "List my Meta
ad accounts". A missing variable is reported by name.

On a Mac, the same `.mcp.json` reuses the credentials that `scripts/setup_claude.py` saved
in `~/.claude.json`, so nothing changes there.

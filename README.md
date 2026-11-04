# Meta Ads + Google Ads MCP servers

Two read-only [MCP](https://modelcontextprotocol.io) servers that let Claude (Claude Code, Claude Desktop, or any MCP client) pull your advertising data:

| Server | Command | Data |
|---|---|---|
| **Meta Ads** | `meta-ads-mcp` | Ad accounts, campaigns, ad sets, ads, creatives, audiences, insights (spend, reach, clicks, conversions, ROAS, with breakdowns) |
| **Google Ads** | `google-ads-mcp` | Accounts/MCC clients, campaigns, ad groups, ads, keywords, search terms, conversion actions, plus any GAQL query |

Neither server can change anything in your ad accounts: every tool only reads.

## Install

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Copy `.env.example` to `.env` and fill in the credentials below. The servers read these from their environment, so you pass them through your MCP client config (see [Connect to Claude](#connect-to-claude)).

## Credentials

### Meta Ads

| Variable | Required | Notes |
|---|---|---|
| `META_ACCESS_TOKEN` | yes | Token with the `ads_read` permission (add `read_insights` / `business_management` if needed) |
| `META_APP_SECRET` | no | If set, every request is signed with `appsecret_proof` |
| `META_API_VERSION` | no | Defaults to `v24.0` |

How to get a long-lived token:
1. Create an app at <https://developers.facebook.com/apps> (type **Business**) and add the **Marketing API** product.
2. Best option: in **Business Settings → Users → System users**, create a system user, assign it your ad accounts, then use **Generate new token** with `ads_read`. System-user tokens don't expire.
3. Quick option for testing: generate a user token in the [Graph API Explorer](https://developers.facebook.com/tools/explorer/) with `ads_read`. It expires within hours unless you exchange it for a long-lived one.

### Google Ads

| Variable | Required | Notes |
|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | yes | From **Tools → API Center** in a Google Ads *manager* account |
| `GOOGLE_ADS_CLIENT_ID` | yes | OAuth client (type **Desktop app**) from Google Cloud Console |
| `GOOGLE_ADS_CLIENT_SECRET` | yes | |
| `GOOGLE_ADS_REFRESH_TOKEN` | yes | Generate it with the helper script below |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | if using an MCC | Your manager account ID, when you reach client accounts through it |
| `GOOGLE_ADS_API_VERSION` | no | Defaults to `v24` |

Steps:
1. In [Google Cloud Console](https://console.cloud.google.com/), enable the **Google Ads API**, then create an OAuth client ID of type **Desktop app**.
2. Get a developer token from your Google Ads manager account (**Tools → API Center**). A token with *Test* access only works on test accounts. Apply for *Basic* access to read real accounts.
3. Get a refresh token:
   ```bash
   GOOGLE_ADS_CLIENT_ID=... GOOGLE_ADS_CLIENT_SECRET=... python scripts/google_ads_refresh_token.py
   ```

## Connect to Claude

### Claude Code

```bash
claude mcp add meta-ads --env META_ACCESS_TOKEN=YOUR_TOKEN -- /path/to/Concept-1/.venv/bin/meta-ads-mcp
```

```bash
claude mcp add google-ads \
  --env GOOGLE_ADS_DEVELOPER_TOKEN=... \
  --env GOOGLE_ADS_CLIENT_ID=... \
  --env GOOGLE_ADS_CLIENT_SECRET=... \
  --env GOOGLE_ADS_REFRESH_TOKEN=... \
  --env GOOGLE_ADS_LOGIN_CUSTOMER_ID=... \
  -- /path/to/Concept-1/.venv/bin/google-ads-mcp
```

### Claude Desktop / other clients

Add the servers to the client's MCP config (for Claude Desktop that's `claude_desktop_config.json`). `mcp.example.json` in this repo has a template.

## Tools

### Meta Ads (`meta-ads`)
- `list_ad_accounts`: every ad account the token can see
- `get_ad_account`: details for one account
- `list_campaigns`, `list_ad_sets`, `list_ads`: optional `effective_status` filter, e.g. `["ACTIVE"]`
- `list_ad_creatives`: copy, headlines, images/videos, links, CTAs
- `get_insights`: metrics for any account, campaign, ad set or ad, at `account`/`campaign`/`adset`/`ad` level, with a `date_preset` or `since`/`until` range, `time_increment` (daily/weekly/monthly), `breakdowns` (age, gender, country, placement, device…), custom `fields`, `filtering` and attribution windows
- `get_account_insights`: the same, for an ad account
- `list_custom_audiences`
- `graph_api_get`: any other read-only Graph API path

### Google Ads (`google-ads`)
- `list_accessible_customers`, `list_client_accounts` (all accounts under an MCC), `get_account_info`
- `get_account_performance` (totals or daily)
- `list_campaigns`, `get_campaign_performance`, `get_ad_group_performance`, `get_ad_performance`
- `get_keyword_performance` (with quality score), `get_search_terms`
- `list_conversion_actions`
- `run_gaql`: any GAQL `SELECT` query
- `describe_gaql_resource`: lists the fields available on a GAQL resource

Date-based tools accept a `date_range` (e.g. `LAST_7_DAYS`, `LAST_MONTH`) or `start_date` + `end_date` (`YYYY-MM-DD`). Rows come back flattened (`campaign.name`, `metrics.clicks`, …). Money values in micros are also returned in currency units (`metrics.costMicros` → `metrics.cost`).

## Example prompts

- "List my Meta ad accounts and show spend by campaign for last month."
- "Break down Meta spend and purchases by age and gender for the last 14 days."
- "Which Google Ads search terms cost the most last week without converting?"
- "Compare daily Google Ads cost and conversions with Meta spend for October 2026."

## Development

```bash
pip install -e '.[dev]'
pytest
```

The tests mock the Meta and Google APIs and call the tools through a real MCP client, so they need no credentials.

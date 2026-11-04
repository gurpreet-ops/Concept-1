"""Meta Ads MCP server: read-only access to ad accounts, campaigns, ads and insights."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ads_mcp.common import check_date
from ads_mcp.meta.client import MetaClient, normalize_account_id

mcp = MCPServer(
    name="meta-ads",
    instructions=(
        "Read-only access to Meta (Facebook/Instagram) Ads data. Start with list_ad_accounts "
        "to find account IDs, then use get_insights for performance metrics (spend, "
        "impressions, clicks, conversions, ROAS) at account/campaign/adset/ad level."
    ),
)

_client: MetaClient | None = None


def client() -> MetaClient:
    global _client
    if _client is None:
        _client = MetaClient.from_env()
    return _client


ACCOUNT_FIELDS = (
    "id,account_id,name,account_status,currency,timezone_name,amount_spent,balance,"
    "spend_cap,business,created_time"
)
CAMPAIGN_FIELDS = (
    "id,name,objective,status,effective_status,buying_type,daily_budget,lifetime_budget,"
    "budget_remaining,bid_strategy,special_ad_categories,start_time,stop_time,created_time,"
    "updated_time"
)
ADSET_FIELDS = (
    "id,name,campaign_id,status,effective_status,optimization_goal,billing_event,bid_amount,"
    "bid_strategy,daily_budget,lifetime_budget,budget_remaining,targeting,promoted_object,"
    "start_time,end_time,created_time,updated_time"
)
AD_FIELDS = (
    "id,name,campaign_id,adset_id,status,effective_status,creative{id,name},"
    "created_time,updated_time"
)
CREATIVE_FIELDS = (
    "id,name,title,body,object_type,call_to_action_type,image_url,thumbnail_url,video_id,"
    "link_url,object_story_spec,asset_feed_spec,effective_object_story_id,instagram_permalink_url"
)

METRIC_FIELDS = [
    "impressions", "reach", "frequency", "clicks", "unique_clicks", "inline_link_clicks",
    "ctr", "cpc", "cpm", "cpp", "spend", "actions", "action_values",
    "cost_per_action_type", "purchase_roas", "video_play_actions",
]
LEVEL_ID_FIELDS = {
    "account": ["account_id", "account_name", "account_currency"],
    "campaign": ["account_id", "campaign_id", "campaign_name", "objective"],
    "adset": ["account_id", "campaign_id", "campaign_name", "adset_id", "adset_name"],
    "ad": ["account_id", "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name"],
}

Level = Literal["account", "campaign", "adset", "ad"]
DatePreset = Literal[
    "today", "yesterday", "this_month", "last_month", "this_quarter", "maximum",
    "last_3d", "last_7d", "last_14d", "last_28d", "last_30d", "last_90d",
    "last_week_mon_sun", "last_week_sun_sat", "last_quarter", "last_year",
    "this_week_mon_today", "this_week_sun_today", "this_year",
]


def _status_filter(statuses: list[str] | None) -> list[dict] | None:
    if not statuses:
        return None
    return [{"field": "effective_status", "operator": "IN", "value": [s.upper() for s in statuses]}]


@mcp.tool()
async def list_ad_accounts(max_results: int = 200) -> list[dict]:
    """List every ad account the access token can see (id, name, status, currency, total spend)."""
    return await client().get_all("me/adaccounts", {"fields": ACCOUNT_FIELDS}, max_results)


@mcp.tool()
async def get_ad_account(account_id: str) -> dict:
    """Get details for one ad account. account_id may be given with or without the 'act_' prefix."""
    return await client().get(normalize_account_id(account_id), {"fields": ACCOUNT_FIELDS})


@mcp.tool()
async def list_campaigns(
    account_id: str, effective_status: list[str] | None = None, max_results: int = 500
) -> list[dict]:
    """List campaigns in an ad account.

    effective_status optionally filters, e.g. ["ACTIVE"] or ["ACTIVE", "PAUSED"].
    """
    params = {"fields": CAMPAIGN_FIELDS, "filtering": _status_filter(effective_status)}
    return await client().get_all(f"{normalize_account_id(account_id)}/campaigns", params, max_results)


@mcp.tool()
async def list_ad_sets(
    account_id: str | None = None,
    campaign_id: str | None = None,
    effective_status: list[str] | None = None,
    max_results: int = 500,
) -> list[dict]:
    """List ad sets for an ad account or a single campaign (pass one of account_id / campaign_id)."""
    parent = _parent(account_id, campaign_id=campaign_id)
    params = {"fields": ADSET_FIELDS, "filtering": _status_filter(effective_status)}
    return await client().get_all(f"{parent}/adsets", params, max_results)


@mcp.tool()
async def list_ads(
    account_id: str | None = None,
    campaign_id: str | None = None,
    adset_id: str | None = None,
    effective_status: list[str] | None = None,
    max_results: int = 500,
) -> list[dict]:
    """List ads for an ad account, campaign or ad set (pass exactly one parent ID)."""
    parent = _parent(account_id, campaign_id=campaign_id, adset_id=adset_id)
    params = {"fields": AD_FIELDS, "filtering": _status_filter(effective_status)}
    return await client().get_all(f"{parent}/ads", params, max_results)


@mcp.tool()
async def list_ad_creatives(
    account_id: str | None = None, ad_id: str | None = None, max_results: int = 200
) -> list[dict]:
    """List ad creatives (copy, headline, image/video, link, CTA) for an ad account or one ad."""
    parent = _parent(account_id, ad_id=ad_id)
    path = f"{parent}/adcreatives"
    return await client().get_all(path, {"fields": CREATIVE_FIELDS}, max_results)


@mcp.tool()
async def get_insights(
    object_id: str,
    level: Level = "campaign",
    date_preset: DatePreset | None = "last_30d",
    since: str | None = None,
    until: str | None = None,
    time_increment: str | None = None,
    breakdowns: list[str] | None = None,
    action_breakdowns: list[str] | None = None,
    fields: list[str] | None = None,
    filtering: list[dict[str, Any]] | None = None,
    action_attribution_windows: list[str] | None = None,
    max_results: int = 1000,
) -> list[dict]:
    """Get performance metrics (spend, impressions, clicks, CTR, CPC, conversions, ROAS ...).

    object_id: an ad account ID (with or without 'act_'), campaign ID, ad set ID or ad ID.
    level: the granularity of returned rows: account, campaign, adset or ad.
    Dates: use date_preset (default last_30d) OR since+until (YYYY-MM-DD), which override the preset.
    time_increment: "1" for daily rows, "7" for weekly, "monthly", or "all_days" (default).
    breakdowns: e.g. ["age","gender"], ["country"], ["publisher_platform","platform_position"],
        ["device_platform"], ["region"].
    action_breakdowns: e.g. ["action_type"] (default by Meta), ["action_device"].
    fields: override the default metric list (Meta insights field names).
    filtering: Meta filter objects, e.g. [{"field":"campaign.name","operator":"CONTAIN","value":"Brand"}].
    action_attribution_windows: e.g. ["7d_click","1d_view"].
    """
    params: dict[str, Any] = {
        "level": level,
        "fields": ",".join(fields or LEVEL_ID_FIELDS[level] + METRIC_FIELDS),
        "breakdowns": ",".join(breakdowns) if breakdowns else None,
        "action_breakdowns": ",".join(action_breakdowns) if action_breakdowns else None,
        "time_increment": time_increment,
        "filtering": filtering,
        "action_attribution_windows": action_attribution_windows,
    }
    if since or until:
        if not (since and until):
            raise ToolError("Provide both since and until, or neither.")
        params["time_range"] = {"since": check_date(since, "since"), "until": check_date(until, "until")}
    elif date_preset:
        params["date_preset"] = date_preset
    return await client().get_all(f"{object_id}/insights", params, max_results)


@mcp.tool()
async def get_account_insights(
    account_id: str,
    level: Level = "campaign",
    date_preset: DatePreset | None = "last_30d",
    since: str | None = None,
    until: str | None = None,
    time_increment: str | None = None,
    breakdowns: list[str] | None = None,
    max_results: int = 1000,
) -> list[dict]:
    """Shortcut for get_insights on an ad account (adds the 'act_' prefix for you)."""
    return await get_insights(
        normalize_account_id(account_id), level=level, date_preset=date_preset, since=since,
        until=until, time_increment=time_increment, breakdowns=breakdowns, max_results=max_results,
    )


@mcp.tool()
async def list_custom_audiences(account_id: str, max_results: int = 200) -> list[dict]:
    """List custom and lookalike audiences in an ad account."""
    fields = "id,name,subtype,description,approximate_count_lower_bound,approximate_count_upper_bound,delivery_status,operation_status,time_created,time_updated"
    return await client().get_all(
        f"{normalize_account_id(account_id)}/customaudiences", {"fields": fields}, max_results
    )


@mcp.tool()
async def graph_api_get(path: str, params: dict[str, Any] | None = None, max_results: int = 500) -> Any:
    """Escape hatch: GET any Graph API path (read-only), e.g. path="act_123/adimages".

    If the response is a list edge (has 'data'), pages are followed up to max_results rows.
    """
    if "access_token" in (params or {}):
        raise ToolError("Do not pass access_token; the server adds it.")
    data = await client().get(path, params)
    if isinstance(data.get("data"), list):
        return await client().follow_pages(data, max_results)
    return data


def _parent(
    account_id: str | None, campaign_id: str | None = None, adset_id: str | None = None,
    ad_id: str | None = None,
) -> str:
    given = [v for v in (campaign_id, adset_id, ad_id) if v]
    if account_id and not given:
        return normalize_account_id(account_id)
    if len(given) == 1 and not account_id:
        return given[0]
    raise ToolError("Pass exactly one of account_id, campaign_id, adset_id or ad_id.")


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()

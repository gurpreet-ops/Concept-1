"""Google Ads MCP server: read-only access to accounts, campaigns and performance via GAQL."""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ads_mcp.common import check_date
from ads_mcp.google.client import GoogleAdsClient, normalize_customer_id

mcp = MCPServer(
    name="google-ads",
    instructions=(
        "Read-only access to Google Ads data. Start with list_accessible_customers (and "
        "list_client_accounts for manager/MCC accounts) to find customer IDs. Use the "
        "*_performance tools for common reports, or run_gaql for any Google Ads Query Language "
        "query. Money fields ending in 'Micros' are also returned in currency units "
        "(e.g. metrics.costMicros -> metrics.cost)."
    ),
)

_client: GoogleAdsClient | None = None


def client() -> GoogleAdsClient:
    global _client
    if _client is None:
        _client = GoogleAdsClient.from_env()
    return _client


DateRange = Literal[
    "TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS", "LAST_BUSINESS_WEEK",
    "THIS_WEEK_SUN_TODAY", "THIS_WEEK_MON_TODAY", "LAST_WEEK_SUN_SAT", "LAST_WEEK_MON_SUN",
    "THIS_MONTH", "LAST_MONTH",
]

CORE_METRICS = (
    "metrics.impressions, metrics.clicks, metrics.ctr, metrics.average_cpc, metrics.cost_micros, "
    "metrics.conversions, metrics.conversions_value, metrics.cost_per_conversion, "
    "metrics.all_conversions, metrics.interactions"
)


def _date_clause(date_range: str | None, start_date: str | None, end_date: str | None) -> str:
    if start_date or end_date:
        if not (start_date and end_date):
            raise ToolError("Provide both start_date and end_date, or neither.")
        return (f"segments.date BETWEEN '{check_date(start_date, 'start_date')}' "
                f"AND '{check_date(end_date, 'end_date')}'")
    return f"segments.date DURING {date_range or 'LAST_30_DAYS'}"


def _where(*clauses: str | None) -> str:
    parts = [c for c in clauses if c]
    return f" WHERE {' AND '.join(parts)}" if parts else ""


def _id_filter(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isdigit():
        raise ToolError(f"{field} must be numeric, got {value!r}")
    return f"{field} = {value}"


@mcp.tool()
async def list_accessible_customers() -> list[str]:
    """List customer IDs directly accessible with the configured OAuth credentials."""
    return await client().list_accessible_customers()


@mcp.tool()
async def list_client_accounts(manager_customer_id: str, include_hidden: bool = False) -> list[dict]:
    """List all accounts under a manager (MCC) account, with name, currency, level and status."""
    query = (
        "SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
        "customer_client.time_zone, customer_client.manager, customer_client.level, "
        "customer_client.status, customer_client.test_account FROM customer_client"
        + ("" if include_hidden else " WHERE customer_client.hidden = FALSE")
    )
    mid = normalize_customer_id(manager_customer_id)
    return await client().search(mid, query, max_rows=10_000, login_customer_id=mid)


@mcp.tool()
async def get_account_info(customer_id: str) -> list[dict]:
    """Basic info for a customer: name, currency, time zone, manager flag, status."""
    query = (
        "SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, "
        "customer.manager, customer.test_account, customer.status, customer.auto_tagging_enabled "
        "FROM customer"
    )
    return await client().search(customer_id, query)


@mcp.tool()
async def get_account_performance(
    customer_id: str,
    date_range: DateRange | None = "LAST_30_DAYS",
    start_date: str | None = None,
    end_date: str | None = None,
    daily: bool = False,
) -> list[dict]:
    """Account-level totals (or one row per day if daily=True) for impressions, clicks, cost, conversions."""
    seg = "segments.date, " if daily else ""
    query = (f"SELECT customer.id, customer.descriptive_name, customer.currency_code, {seg}{CORE_METRICS} "
             f"FROM customer{_where(_date_clause(date_range, start_date, end_date))}")
    if daily:
        query += " ORDER BY segments.date"
    return await client().search(customer_id, query, max_rows=10_000)


@mcp.tool()
async def list_campaigns(customer_id: str, include_removed: bool = False) -> list[dict]:
    """List campaigns with status, channel type, bidding strategy and daily budget."""
    query = (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "campaign.advertising_channel_sub_type, campaign.bidding_strategy_type, campaign.start_date, "
        "campaign.end_date, campaign_budget.amount_micros, campaign_budget.delivery_method FROM campaign"
        + ("" if include_removed else " WHERE campaign.status != 'REMOVED'")
        + " ORDER BY campaign.name"
    )
    return await client().search(customer_id, query, max_rows=10_000)


@mcp.tool()
async def get_campaign_performance(
    customer_id: str,
    date_range: DateRange | None = "LAST_30_DAYS",
    start_date: str | None = None,
    end_date: str | None = None,
    daily: bool = False,
    campaign_id: str | None = None,
    max_rows: int = 1000,
) -> list[dict]:
    """Campaign metrics: impressions, clicks, CTR, avg CPC, cost, conversions, conversion value,
    search impression share. Set daily=True for one row per campaign per day."""
    seg = "segments.date, " if daily else ""
    query = (
        f"SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, {seg}"
        f"{CORE_METRICS}, metrics.search_impression_share FROM campaign"
        + _where(_date_clause(date_range, start_date, end_date), _id_filter("campaign.id", campaign_id))
        + (" ORDER BY segments.date" if daily else " ORDER BY metrics.cost_micros DESC")
    )
    return await client().search(customer_id, query, max_rows)


@mcp.tool()
async def get_ad_group_performance(
    customer_id: str,
    date_range: DateRange | None = "LAST_30_DAYS",
    start_date: str | None = None,
    end_date: str | None = None,
    campaign_id: str | None = None,
    max_rows: int = 1000,
) -> list[dict]:
    """Ad group metrics, optionally limited to one campaign."""
    query = (
        f"SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.status, "
        f"ad_group.type, {CORE_METRICS} FROM ad_group"
        + _where(_date_clause(date_range, start_date, end_date), _id_filter("campaign.id", campaign_id))
        + " ORDER BY metrics.cost_micros DESC"
    )
    return await client().search(customer_id, query, max_rows)


@mcp.tool()
async def get_ad_performance(
    customer_id: str,
    date_range: DateRange | None = "LAST_30_DAYS",
    start_date: str | None = None,
    end_date: str | None = None,
    campaign_id: str | None = None,
    max_rows: int = 1000,
) -> list[dict]:
    """Ad-level metrics with ad type, final URLs and responsive search ad headlines/descriptions."""
    query = (
        "SELECT campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.name, "
        "ad_group_ad.ad.type, ad_group_ad.status, ad_group_ad.ad.final_urls, "
        "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
        f"{CORE_METRICS} FROM ad_group_ad"
        + _where(_date_clause(date_range, start_date, end_date), _id_filter("campaign.id", campaign_id))
        + " ORDER BY metrics.cost_micros DESC"
    )
    return await client().search(customer_id, query, max_rows)


@mcp.tool()
async def get_keyword_performance(
    customer_id: str,
    date_range: DateRange | None = "LAST_30_DAYS",
    start_date: str | None = None,
    end_date: str | None = None,
    campaign_id: str | None = None,
    max_rows: int = 1000,
) -> list[dict]:
    """Keyword metrics with match type, status and quality score."""
    query = (
        "SELECT campaign.name, ad_group.name, ad_group_criterion.criterion_id, "
        "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "ad_group_criterion.status, ad_group_criterion.quality_info.quality_score, "
        f"{CORE_METRICS} FROM keyword_view"
        + _where(_date_clause(date_range, start_date, end_date), _id_filter("campaign.id", campaign_id))
        + " ORDER BY metrics.cost_micros DESC"
    )
    return await client().search(customer_id, query, max_rows)


@mcp.tool()
async def get_search_terms(
    customer_id: str,
    date_range: DateRange | None = "LAST_30_DAYS",
    start_date: str | None = None,
    end_date: str | None = None,
    campaign_id: str | None = None,
    max_rows: int = 1000,
) -> list[dict]:
    """Actual search queries that triggered ads, with the matched campaign/ad group and metrics."""
    query = (
        "SELECT search_term_view.search_term, search_term_view.status, campaign.name, ad_group.name, "
        f"{CORE_METRICS} FROM search_term_view"
        + _where(_date_clause(date_range, start_date, end_date), _id_filter("campaign.id", campaign_id))
        + " ORDER BY metrics.impressions DESC"
    )
    return await client().search(customer_id, query, max_rows)


@mcp.tool()
async def list_conversion_actions(customer_id: str) -> list[dict]:
    """List conversion actions (name, category, type, status, counting, primary-for-goal)."""
    query = (
        "SELECT conversion_action.id, conversion_action.name, conversion_action.category, "
        "conversion_action.type, conversion_action.status, conversion_action.counting_type, "
        "conversion_action.primary_for_goal FROM conversion_action"
    )
    return await client().search(customer_id, query, max_rows=10_000)


@mcp.tool()
async def run_gaql(customer_id: str, query: str, max_rows: int = 1000,
                   login_customer_id: str | None = None) -> list[dict]:
    """Run any Google Ads Query Language (GAQL) SELECT query and return flattened rows.

    Example: SELECT campaign.name, segments.device, metrics.clicks FROM campaign
             WHERE segments.date DURING LAST_7_DAYS
    Use describe_gaql_resource to discover selectable fields.
    login_customer_id overrides GOOGLE_ADS_LOGIN_CUSTOMER_ID for this call.
    """
    if not query.lstrip().upper().startswith("SELECT"):
        raise ToolError("Only SELECT queries are supported.")
    return await client().search(customer_id, query, max_rows, login_customer_id)


@mcp.tool()
async def describe_gaql_resource(resource: str) -> list[dict]:
    """List the selectable/filterable fields for a GAQL resource (e.g. 'campaign', 'ad_group_ad')
    or details for one field (e.g. 'metrics.conversions')."""
    if not resource.replace("_", "").replace(".", "").isalnum():
        raise ToolError(f"Invalid resource name {resource!r}")
    if "." in resource:
        where = f"name = '{resource}'"
    else:
        where = f"name LIKE '{resource}.%'"
    query = (f"SELECT name, category, data_type, selectable, filterable, sortable, is_repeated "
             f"WHERE {where}")
    return await client().search_fields(query)


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()

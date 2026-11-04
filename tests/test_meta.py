import json

import httpx
from mcp import Client

from ads_mcp.meta import server
from ads_mcp.meta.client import MetaClient


def make_client(handler):
    return MetaClient("tok", app_secret="sec", transport=httpx.MockTransport(handler))


async def test_insights_paginates_and_builds_params():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url)
        if "after" not in request.url.params:
            return httpx.Response(200, json={
                "data": [{"campaign_id": "1", "spend": "10"}],
                "paging": {"next": "https://graph.facebook.com/v24.0/act_9/insights?after=x&access_token=tok"},
            })
        return httpx.Response(200, json={"data": [{"campaign_id": "2", "spend": "5"}]})

    server._client = make_client(handler)
    async with Client(server.mcp) as c:
        result = await c.call_tool("get_account_insights", {
            "account_id": "9", "since": "2026-10-01", "until": "2026-10-31", "time_increment": "1",
        })
    assert not result.is_error, result
    rows = result.structured_content["result"]
    assert [r["campaign_id"] for r in rows] == ["1", "2"]
    first = calls[0]
    assert first.path == "/v24.0/act_9/insights"
    assert json.loads(first.params["time_range"]) == {"since": "2026-10-01", "until": "2026-10-31"}
    assert "date_preset" not in first.params
    assert first.params["level"] == "campaign"
    assert "appsecret_proof" in first.params


async def test_api_error_reaches_model():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token", "code": 190}})

    server._client = make_client(handler)
    async with Client(server.mcp) as c:
        result = await c.call_tool("list_ad_accounts", {})
    assert result.is_error
    assert "Invalid OAuth access token" in result.content[0].text


async def test_parent_validation():
    server._client = make_client(lambda r: httpx.Response(200, json={"data": []}))
    async with Client(server.mcp) as c:
        result = await c.call_tool("list_ads", {"account_id": "1", "adset_id": "2"})
    assert result.is_error
    assert "exactly one" in result.content[0].text

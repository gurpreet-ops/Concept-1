import json

import httpx
from mcp import Client

from ads_mcp.google import server
from ads_mcp.google.client import GoogleAdsClient


def make_client(handler):
    return GoogleAdsClient("dev", "cid", "secret", "refresh", login_customer_id="111-222-3333",
                           transport=httpx.MockTransport(handler))


async def test_campaign_performance_flattens_and_pages():
    seen = []

    def handler(request: httpx.Request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        body = json.loads(request.content)
        seen.append((request, body))
        if "pageToken" not in body:
            return httpx.Response(200, json={
                "results": [{"campaign": {"id": "1", "name": "A"}, "metrics": {"costMicros": "2500000"}}],
                "nextPageToken": "p2",
            })
        return httpx.Response(200, json={"results": [{"campaign": {"id": "2", "name": "B"}, "metrics": {}}]})

    server._client = make_client(handler)
    async with Client(server.mcp) as c:
        result = await c.call_tool("get_campaign_performance", {
            "customer_id": "123-456-7890", "start_date": "2026-10-01", "end_date": "2026-10-31",
        })
    assert not result.is_error, result
    rows = result.structured_content["result"]
    assert rows[0]["campaign.name"] == "A"
    assert rows[0]["metrics.cost"] == 2.5
    assert len(rows) == 2
    req, body = seen[0]
    assert req.url.path == "/v24/customers/1234567890/googleAds:search"
    assert req.headers["developer-token"] == "dev"
    assert req.headers["login-customer-id"] == "1112223333"
    assert req.headers["authorization"] == "Bearer at"
    assert "BETWEEN '2026-10-01' AND '2026-10-31'" in body["query"]


async def test_rejects_bad_input():
    server._client = make_client(lambda r: httpx.Response(500))
    async with Client(server.mcp) as c:
        bad_date = await c.call_tool("get_campaign_performance", {
            "customer_id": "1", "start_date": "2026-10-01' OR 1=1", "end_date": "2026-10-31"})
        mutate = await c.call_tool("run_gaql", {"customer_id": "1", "query": "DELETE campaign"})
    assert bad_date.is_error and "YYYY-MM-DD" in bad_date.content[0].text
    assert mutate.is_error and "SELECT" in mutate.content[0].text


async def test_api_error_message():
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED", "message": "denied",
            "details": [{"errors": [{"errorCode": {"authorizationError": "USER_PERMISSION_DENIED"},
                                     "message": "User doesn't have permission"}]}]}})

    server._client = make_client(handler)
    async with Client(server.mcp) as c:
        result = await c.call_tool("list_accessible_customers", {})
    assert result.is_error
    assert "User doesn't have permission" in result.content[0].text

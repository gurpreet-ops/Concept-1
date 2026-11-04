"""Thin async client for the Google Ads REST API."""

from __future__ import annotations

import os
import time

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from ads_mcp.common import flatten, require_env

DEFAULT_API_VERSION = "v24"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def normalize_customer_id(customer_id: str) -> str:
    cid = customer_id.replace("-", "").strip()
    if not cid.isdigit():
        raise ToolError(f"Invalid customer ID {customer_id!r}; expected digits like 123-456-7890.")
    return cid


def _add_currency_units(row: dict) -> dict:
    """For every '...Micros' value, add the same value in currency units (e.g. costMicros -> cost)."""
    for key in list(row):
        if key.endswith("Micros") and row[key] is not None:
            try:
                row[key[: -len("Micros")]] = int(row[key]) / 1_000_000
            except (TypeError, ValueError):
                pass
    return row


class GoogleAdsClient:
    def __init__(
        self,
        developer_token: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        login_customer_id: str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.developer_token = developer_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.login_customer_id = normalize_customer_id(login_customer_id) if login_customer_id else None
        self.base_url = f"https://googleads.googleapis.com/{api_version}"
        self._http = httpx.AsyncClient(timeout=120, transport=transport)
        self._access_token: str | None = None
        self._expires_at = 0.0

    @classmethod
    def from_env(cls) -> GoogleAdsClient:
        return cls(
            developer_token=require_env("GOOGLE_ADS_DEVELOPER_TOKEN"),
            client_id=require_env("GOOGLE_ADS_CLIENT_ID"),
            client_secret=require_env("GOOGLE_ADS_CLIENT_SECRET"),
            refresh_token=require_env("GOOGLE_ADS_REFRESH_TOKEN"),
            login_customer_id=os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None,
            api_version=os.environ.get("GOOGLE_ADS_API_VERSION", DEFAULT_API_VERSION),
        )

    async def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
        )
        data = resp.json()
        if resp.status_code >= 400:
            raise ToolError(f"Google OAuth token refresh failed: {data.get('error_description') or data}")
        self._access_token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    async def _headers(self, login_customer_id: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {await self._token()}",
            "developer-token": self.developer_token,
        }
        login = normalize_customer_id(login_customer_id) if login_customer_id else self.login_customer_id
        if login:
            headers["login-customer-id"] = login
        return headers

    async def _call(self, method: str, path: str, json: dict | None = None,
                    login_customer_id: str | None = None) -> dict:
        resp = await self._http.request(
            method, f"{self.base_url}/{path}", json=json, headers=await self._headers(login_customer_id)
        )
        if resp.status_code >= 400:
            raise ToolError(_format_error(resp))
        return resp.json()

    async def list_accessible_customers(self) -> list[str]:
        data = await self._call("GET", "customers:listAccessibleCustomers")
        return [name.split("/")[-1] for name in data.get("resourceNames", [])]

    async def search(self, customer_id: str, query: str, max_rows: int = 1000,
                     login_customer_id: str | None = None) -> list[dict]:
        """Run a GAQL query, following page tokens, and return flattened rows."""
        cid = normalize_customer_id(customer_id)
        rows: list[dict] = []
        body: dict = {"query": query}
        while len(rows) < max_rows:
            data = await self._call("POST", f"customers/{cid}/googleAds:search", body, login_customer_id)
            rows.extend(_add_currency_units(flatten(r)) for r in data.get("results", []))
            token = data.get("nextPageToken")
            if not token:
                break
            body = {"query": query, "pageToken": token}
        return rows[:max_rows]

    async def search_fields(self, query: str) -> list[dict]:
        data = await self._call("POST", "googleAdsFields:search", {"query": query})
        return data.get("results", [])

    async def aclose(self) -> None:
        await self._http.aclose()


def _format_error(resp: httpx.Response) -> str:
    try:
        err = resp.json()
        if isinstance(err, list):
            err = err[0]
        err = err.get("error", err)
    except ValueError:
        return f"Google Ads API error (HTTP {resp.status_code}): {resp.text[:500]}"
    messages = []
    for detail in err.get("details", []):
        for e in detail.get("errors", []):
            messages.append(f"{e.get('errorCode')}: {e.get('message')}")
    summary = "; ".join(messages) or err.get("message", "")
    return f"Google Ads API error (HTTP {resp.status_code}, {err.get('status')}): {summary}"

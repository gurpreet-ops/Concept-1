"""Thin async client for the Meta Marketing API (Graph API)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from ads_mcp.common import require_env

DEFAULT_API_VERSION = "v24.0"

# Meta access tokens start with "EAA". Meta sometimes echoes the token back in error
# messages ("Malformed access token EAA..."), so scrub anything token-shaped.
_TOKEN_RE = re.compile(r"EAA[A-Za-z0-9]{20,}")


def redact(text: str) -> str:
    return _TOKEN_RE.sub("[token hidden]", text)


def normalize_account_id(account_id: str) -> str:
    account_id = account_id.strip()
    return account_id if account_id.startswith("act_") else f"act_{account_id}"


class MetaClient:
    def __init__(
        self,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        app_secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.access_token = access_token
        self.app_secret = app_secret
        self.base_url = f"https://graph.facebook.com/{api_version}"
        self._http = httpx.AsyncClient(timeout=60, transport=transport)

    @classmethod
    def from_env(cls) -> MetaClient:
        return cls(
            access_token=require_env("META_ACCESS_TOKEN"),
            api_version=os.environ.get("META_API_VERSION", DEFAULT_API_VERSION),
            app_secret=os.environ.get("META_APP_SECRET") or None,
        )

    def _auth_params(self) -> dict[str, str]:
        params = {"access_token": self.access_token}
        if self.app_secret:
            params["appsecret_proof"] = hmac.new(
                self.app_secret.encode(), self.access_token.encode(), hashlib.sha256
            ).hexdigest()
        return params

    @staticmethod
    def _encode(params: dict[str, Any]) -> dict[str, str]:
        out = {}
        for key, value in params.items():
            if value is None:
                continue
            out[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        return out

    async def _request(self, url: str, params: dict[str, Any] | None) -> dict:
        resp = await self._http.get(url, params=params)
        try:
            data = resp.json()
        except ValueError:
            raise ToolError(f"Meta API returned non-JSON response (HTTP {resp.status_code})")
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise ToolError(redact(
                f"Meta API error (HTTP {resp.status_code}): {err.get('message', data)} "
                f"[type={err.get('type')}, code={err.get('code')}, subcode={err.get('error_subcode')}]"
            ))
        return data

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        query = {**self._encode(params or {}), **self._auth_params()}
        return await self._request(f"{self.base_url}/{path.lstrip('/')}", query)

    async def get_all(
        self, path: str, params: dict[str, Any] | None = None, max_results: int = 500
    ) -> list[dict]:
        """GET an edge and follow `paging.next` until max_results rows are collected."""
        params = {"limit": min(max_results, 500), **(params or {})}
        return await self.follow_pages(await self.get(path, params), max_results)

    async def follow_pages(self, page: dict, max_results: int) -> list[dict]:
        rows: list[dict] = list(page.get("data", []))
        while len(rows) < max_results:
            next_url = page.get("paging", {}).get("next")
            if not next_url:
                break
            # `next` already carries every query param, including the access token.
            page = await self._request(next_url, None)
            rows.extend(page.get("data", []))
        return rows[:max_results]

    async def aclose(self) -> None:
        await self._http.aclose()

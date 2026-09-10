"""
Adapter layer for the Urja Meter Ops portal.

Handles authentication, session management, auto-reauthentication,
and all raw calls to the portal's internal endpoints.
"""

import hashlib
import hmac
import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

PORTAL_BASE = os.getenv("PORTAL_BASE", "https://urja-ops.flockenergy.tech")
LOGIN_EMAIL = os.getenv("PORTAL_EMAIL", "operator@urja.local")
LOGIN_PASSWORD = os.getenv("PORTAL_PASSWORD", "urja-ops-2026")


class PortalClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=PORTAL_BASE, follow_redirects=True)
        self._authenticated = False

    async def _login(self) -> None:
        resp = await self._client.post(
            "/login",
            data={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
            headers={
                "Origin": PORTAL_BASE,
                "Referer": f"{PORTAL_BASE}/login",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp.raise_for_status()
        self._authenticated = True

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        if not self._authenticated:
            await self._login()
        resp = await self._client.get(path, **kwargs)
        # Session expired — portal redirects to /login
        if resp.status_code in (401, 403) or (
            resp.status_code == 200 and "Sign in" in resp.text[:500]
        ):
            self._authenticated = False
            await self._login()
            resp = await self._client.get(path, **kwargs)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------ #
    # Meters                                                               #
    # ------------------------------------------------------------------ #

    async def search_meters(self, q: str = "", page: int = 1) -> dict[str, Any]:
        resp = await self._get(
            "/portal/meters/search",
            params={"q": q, "page": page},
        )
        return resp.json()

    async def get_meter_detail(self, meter_id: str) -> dict[str, Any]:
        """
        The portal has no dedicated JSON endpoint for meter detail.
        SvelteKit embeds the server-side data in the page via __data.json.
        """
        resp = await self._get(f"/meters/{meter_id}/__data.json")
        raw = resp.json()
        # Node index 2 carries the meter payload
        node = next(
            (n for n in raw.get("nodes", []) if n and n.get("type") == "data" and "meterId" in str(n.get("data", ""))),
            None,
        )
        if node is None:
            return {}
        return _decode_sveltekit_data(node["data"])

    async def get_meter_geo(self, meter_id: str) -> dict[str, Any]:
        resp = await self._get(f"/portal/meters/{meter_id}/geo")
        return resp.json()

    async def get_meter_energy(self, meter_id: str) -> dict[str, Any]:
        resp = await self._get(f"/portal/meters/{meter_id}/energy")
        return resp.json()

    # ------------------------------------------------------------------ #
    # Distribution Transformers                                            #
    # ------------------------------------------------------------------ #

    async def list_dts(self, page: int = 1) -> dict[str, Any]:
        resp = await self._get("/portal/dts", params={"page": page})
        return resp.json()

    # ------------------------------------------------------------------ #
    # Bulk export (HMAC-signed)                                            #
    # ------------------------------------------------------------------ #

    async def _signing_secret(self) -> str:
        resp = await self._get("/portal/keys")
        return resp.json()["data"]["signingSecret"]

    async def export_meters(self, page: int = 1) -> dict[str, Any]:
        secret = await self._signing_secret()
        query = f"page={page}"
        ts = str(int(time.time()))
        msg = "\n".join(["GET", "/portal/export", query, ts])
        sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        resp = await self._get(
            "/portal/export",
            params={"page": page},
            headers={"x-timestamp": ts, "x-signature": sig},
        )
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()


# ------------------------------------------------------------------ #
# SvelteKit __data.json decoder                                        #
# ------------------------------------------------------------------ #

def _decode_sveltekit_data(data: list) -> dict:
    """
    SvelteKit serialises server data as a de-duplicated array where
    index 0 is a template object with integer references into the array.
    This resolves those references back into a plain dict.
    """
    def resolve(val):
        if isinstance(val, int) and 0 <= val < len(data):
            return resolve(data[val])
        if isinstance(val, dict):
            return {k: resolve(v) for k, v in val.items()}
        if isinstance(val, list):
            return [resolve(v) for v in val]
        return val

    if not data:
        return {}
    return resolve(data[0])

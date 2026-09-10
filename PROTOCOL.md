# PROTOCOL.md — Urja Meter Ops Portal: How It Works

## Overview

The portal is a **SvelteKit** single-page application served from `https://urja-ops.flockenergy.tech`. It uses server-side rendering (SSR) for initial page loads and client-side `fetch` calls for dynamic data. There is no public API documentation, but the internal endpoints are discoverable by inspecting the compiled JavaScript bundles.

---

## Authentication

### Mechanism
- **Session cookie** — `__Secure-better-auth.session_token` (HttpOnly, Secure, SameSite=Lax)
- The library in use appears to be [better-auth](https://github.com/better-auth/better-auth) based on the cookie name.
- Session TTL: **3600 seconds** (1 hour), set via `Max-Age` in the `Set-Cookie` header.

### Login flow
```
POST /login
Content-Type: application/x-www-form-urlencoded
Origin: https://urja-ops.flockenergy.tech
Referer: https://urja-ops.flockenergy.tech/login

email=operator%40urja.local&password=urja-ops-2026
```

**Response (200 OK):**
```json
{"type": "redirect", "status": 303, "location": "/meters"}
```
The session cookie is set in the `Set-Cookie` response header. All subsequent requests must include this cookie.

**CSRF protection:** The portal rejects cross-origin POST requests with `403 Cross-site POST form submissions are forbidden`. The `Origin` and `Referer` headers must match the portal's own origin.

### Sign-out
```
POST /api/auth/sign-out
```
Invalidates the session server-side.

---

## Internal Endpoints

All endpoints below require the session cookie. They return JSON unless noted.

### Meters

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portal/meters/search?q=&page=1` | Paginated meter list (20/page). `q` searches meter ID and serial number. |
| GET | `/portal/meters/{id}/geo` | Geographic coordinates for a meter. |
| GET | `/portal/meters/{id}/energy` | Half-hourly energy readings (~7-day window). |

**`/portal/meters/search` response:**
```json
{
  "data": [
    {"meterId": "J100001", "serialNo": "GE84132", "make": "L&T",
     "phaseType": "single", "installStatus": "Installed", "dtCode": "DT-002"}
  ],
  "total": 403,
  "page": 1,
  "pageSize": 20
}
```

**`/portal/meters/{id}/geo` response:**
```json
{"data": {"latitude": "26.822136543835608", "longitude": "75.90718190602279"}}
```
Note: latitude and longitude are returned as **strings**, not numbers.

**`/portal/meters/{id}/energy` response:**
```json
{
  "data": [
    {"timestamp": "23/06/2026 23:30", "kwh": "42594.05", "kvah": "46001.58", "voltR": "231"}
  ]
}
```
- Timestamps are in `DD/MM/YYYY HH:MM` format (local time, no timezone).
- `kwh` and `kvah` are cumulative register values (not interval deltas), returned as strings.
- `voltR` is R-phase voltage in volts, returned as a string integer.
- The window appears to be approximately 7 days of 30-minute readings (~336 records).

### Meter Detail (SSR via SvelteKit `__data.json`)

There is no dedicated JSON endpoint for meter nameplate and hierarchy data. Instead, SvelteKit exposes its server-side load data via:

```
GET /meters/{id}/__data.json
```

This returns a de-duplicated array format specific to SvelteKit's serialisation protocol. The payload at node index 2 contains:
- `meterId` — the meter ID
- `detail.data` — array of `{parameterName, parameterValue}` objects (nameplate fields)
- `hierarchy` — flat object with keys: `Meter ID`, `Installation Status`, `Installation Type`, `Zone`, `Circle`, `Division`, `Subdivision`, `Sub Station`, `Feeder`, `DT`

The SvelteKit de-duplication format uses integer indices as references into the top-level array. A resolver is needed to reconstruct the full object.

### Distribution Transformers

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portal/dts?page=1` | Paginated DT list (20/page). |

**Response:**
```json
{
  "data": [
    {"code": "DT-001", "name": "Malviya Nagar DT 1", "feederCode": "F-001", "capacityKva": 100}
  ],
  "total": 40,
  "page": 1,
  "pageSize": 20
}
```
There are **40 distribution transformers** in total.

### Bulk Export (HMAC-signed)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portal/keys` | Returns the HMAC signing secret. |
| GET | `/portal/export?page=1` | Full meter export (requires HMAC signature). |

**Signing algorithm** (discovered from the compiled JS in `nodes/4.2Bgc2kUI.js`):

```
message = METHOD + "\n" + PATH + "\n" + QUERY_STRING + "\n" + UNIX_TIMESTAMP
signature = HMAC-SHA256(message, signingSecret).hexdigest()
```

Headers required:
- `x-timestamp: <unix_timestamp_seconds>`
- `x-signature: <hex_hmac>`

The signing secret is fetched from `/portal/keys` (requires auth). The timestamp must be recent (the portal likely enforces a replay window, though the exact tolerance is unknown — I used ≤60 s in testing).

**Export response** (all 403 meters on page 1):
```json
{
  "data": [
    {
      "meterId": "J100000",
      "serialNo": "SE33962",
      "make": "HPL",
      "phaseType": "single",
      "installStatus": "Decommissioned",
      "installType": "Whole Current",
      "build": "legacy",
      "dtCode": "DT-001",
      "hierarchy": {
        "zone": {"name": "Jaipur Zone 1", "code": "Z-01"},
        "circle": {"name": "Circle 1", "code": "C-01"},
        "division": {"name": "Division 1", "code": "D-01"},
        "subdivision": {"name": "Subdivision 1", "code": "SD-01"},
        "substation": {"name": "Substation 1", "code": "SS-01"},
        "feeder": {"name": "Feeder 1", "code": "F-001"},
        "dt": {"name": "Malviya Nagar DT 1", "code": "DT-001"}
      },
      "geo": {"lat": 26.938961, "lng": 75.830956}
    }
  ],
  "total": 403
}
```

Note: the export uses `lat`/`lng` (floats), while the per-meter geo endpoint uses `latitude`/`longitude` (strings). Inconsistency in the portal's own API.

---

## Network Hierarchy

The hierarchy has **7 levels**:

```
Zone → Circle → Division → Subdivision → Substation → Feeder → DT → Meter
```

From the data:
- **2 zones** (Jaipur Zone 1, Jaipur Zone 2)
- **40 DTs** across multiple feeders
- **403 meters** total

The hierarchy is fully reconstructable from the bulk export endpoint.

---

## Quirks and Surprises

1. **Numeric values as strings** — `kwh`, `kvah`, `voltR`, `latitude`, `longitude` are all returned as strings by the portal's internal endpoints. The export endpoint returns `lat`/`lng` as actual floats.

2. **SvelteKit `__data.json` de-duplication** — The meter detail data is not available via a clean JSON API; it's embedded in SvelteKit's internal serialisation format which requires a custom resolver.

3. **HMAC-signed export** — The bulk export endpoint requires a request signature, but the signing secret is freely available to any authenticated user via `/portal/keys`. This is security theatre — it prevents casual scraping but not a determined client.

4. **No pagination on export** — All 403 meters are returned on page 1 of the export. The `page` parameter exists but appears to have no effect at this dataset size.

5. **`build` field** — Some meters have `"build": "legacy"`. The detail endpoint for these meters may return a `classData` JSON blob instead of the standard `{parameterName, parameterValue}` array. The JS source shows a branch for this case.

6. **Session expiry** — The session cookie has a 1-hour TTL. The portal returns a JSON redirect body (not an HTTP 401) when the session expires, so expiry detection requires checking the response body.

7. **Timestamp format** — Energy readings use `DD/MM/YYYY HH:MM` (day-first, no timezone). This is non-standard and requires careful parsing.

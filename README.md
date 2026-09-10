# Urja Meter Ops API

A clean, documented REST API wrapper over the [Urja Meter Ops](https://urja-ops.flockenergy.tech) portal — a legacy SvelteKit web application used by field and operations staff to look up smart-meter information.

The service handles authentication, session management, and data normalisation so that downstream clients never need to touch the original portal.

---

## What's in this repo

```
urja-api/
├── main.py              # FastAPI application — all routes
├── portal_client.py     # Adapter layer — all portal communication
├── requirements.txt
├── .env.example         # Environment variable template
├── generate_openapi.py  # Generates openapi.json without running the server
├── openapi.json         # OpenAPI 3.1 spec (auto-generated)
├── PROTOCOL.md          # How the portal actually works (discovery notes)
├── REFLECTION.md        # Reflection questions answered
└── README.md            # This file
```

---

## Quick start

```bash
# 1. Clone and enter the directory
git clone <repo-url>
cd urja-api

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials (defaults are already set for the test portal)
cp .env.example .env
# Edit .env if needed

# 4. Run the server
python -m uvicorn main:app --reload

# 5. Open the interactive docs
open http://localhost:8000/docs
```

The API will be available at `http://localhost:8000`.  
Interactive Swagger UI docs: `http://localhost:8000/docs`  
OpenAPI JSON: `http://localhost:8000/openapi.json`

---

## Sample requests

### List meters (paginated, searchable)
```bash
curl "http://localhost:8000/api/v1/meters?page=1"
curl "http://localhost:8000/api/v1/meters?q=J100001"
```

**Response:**
```json
{
  "data": [
    {
      "meter_id": "J100001",
      "serial_no": "GE84132",
      "make": "L&T",
      "phase_type": "single",
      "install_status": "Installed",
      "dt_code": "DT-002"
    }
  ],
  "total": 403,
  "page": 1,
  "page_size": 20
}
```

### Get meter detail
```bash
curl "http://localhost:8000/api/v1/meters/J100001"
```

**Response:**
```json
{
  "meter_id": "J100001",
  "nameplate": {
    "meter_id": "J100001",
    "serial_no": "GE84132",
    "make": "L&T",
    "phase_type": "single",
    "installation_status": "Installed",
    "installation_type": "CT Operated"
  },
  "hierarchy": {
    "zone": "Jaipur Zone 2 (Z-02)",
    "circle": "Circle 2 (C-02)",
    "division": "Division 2 (D-02)",
    "subdivision": "Subdivision 2 (SD-02)",
    "substation": "Substation 2 (SS-02)",
    "feeder": "Feeder 2 (F-002)",
    "dt": "Mansarovar DT 2 (DT-002)"
  },
  "location": {
    "latitude": 26.822136543835608,
    "longitude": 75.90718190602279
  }
}
```

### Get consumption history
```bash
curl "http://localhost:8000/api/v1/meters/J100001/consumption"
```

**Response:**
```json
{
  "meter_id": "J100001",
  "count": 337,
  "readings": [
    {
      "timestamp": "23/06/2026 23:30",
      "kwh": 42594.05,
      "kvah": 46001.58,
      "volt_r": 231
    }
  ]
}
```

### List distribution transformers
```bash
curl "http://localhost:8000/api/v1/transformers?page=1"
```

### Get network hierarchy tree
```bash
curl "http://localhost:8000/api/v1/hierarchy"
```

### Full meter export (with hierarchy + geo)
```bash
curl "http://localhost:8000/api/v1/export"
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/meters` | Paginated meter list. Query params: `q` (search), `page`. |
| GET | `/api/v1/meters/{meter_id}` | Full meter detail: nameplate, hierarchy, location. |
| GET | `/api/v1/meters/{meter_id}/consumption` | Half-hourly energy readings (~7-day window). |
| GET | `/api/v1/transformers` | Paginated distribution transformer list. |
| GET | `/api/v1/hierarchy` | Full Zone→DT network hierarchy tree. |
| GET | `/api/v1/export` | All meters with nameplate, hierarchy, and geo. |

Full spec: see [`openapi.json`](./openapi.json) or browse `/docs` when the server is running.

---

## Architecture

```
Client → FastAPI (main.py) → PortalClient (portal_client.py) → Urja Portal
```

**`portal_client.py`** is the adapter layer. It owns:
- A persistent `httpx.AsyncClient` that stores the session cookie
- Login and auto-reauthentication (detects expired sessions by checking for a redirect body)
- All raw HTTP calls to the portal's internal endpoints
- HMAC signature generation for the bulk export endpoint
- SvelteKit `__data.json` response decoding

**`main.py`** is the API layer. It owns:
- FastAPI route definitions with OpenAPI annotations
- Data normalisation (snake_case keys, string→float/int coercion, hierarchy tree construction)
- Error mapping (portal errors → HTTP 502)

The two layers are intentionally separate so the adapter can be tested or replaced independently.

---

## Assumptions

- **Credentials are stable.** The `.env` file holds the portal credentials. If they change, update `.env`.
- **Energy readings are cumulative register values**, not interval deltas. The kWh values increase monotonically. Consumers wanting interval consumption should compute `readings[n].kwh - readings[n-1].kwh`.
- **Timestamps are IST (UTC+5:30).** The portal returns no timezone information. Jaipur is in India, so IST is the reasonable assumption. The API passes timestamps through as-is (`DD/MM/YYYY HH:MM`).
- **All 403 meters fit on one export page.** The export endpoint returns all records on page 1 at the current dataset size.

---

## Design decisions and trade-offs

**Why FastAPI?** Auto-generates OpenAPI spec, async-native (matches `httpx.AsyncClient`), minimal boilerplate.

**Why `httpx` over `requests`?** Async support is essential — FastAPI routes are async, and `requests` would block the event loop.

**Why use `__data.json` for meter detail?** The portal has no dedicated JSON endpoint for meter nameplate/hierarchy data. The SvelteKit `__data.json` endpoint is the cleanest available path. The alternative (parsing the SSR HTML) is more fragile.

**No caching.** Every API call proxies to the portal. This is fine for the current use case (low-traffic internal tool) and keeps the service stateless. With higher traffic, a 5-minute TTL cache on the export and hierarchy endpoints would be the first thing to add.

**Normalisation choices:**
- Portal uses camelCase; API uses snake_case (more Pythonic, more REST-conventional)
- Numeric strings are coerced to float/int at the API boundary
- `voltR` → `volt_r` (the portal only returns R-phase voltage; Y and B phases are absent)

---

## What I intentionally left out

- **Caching** — noted above; would be the first addition with more time
- **ISO 8601 timestamp normalisation** — the portal's `DD/MM/YYYY HH:MM` format is passed through. Parsing to ISO 8601 with timezone would be better but requires an assumption about the timezone.
- **Interval consumption deltas** — the API exposes raw cumulative readings; delta computation is left to the consumer
- **Authentication endpoint** — the service manages the portal session internally; there's no `/auth/login` endpoint on the wrapper API. This is intentional: the wrapper is a service account, not a multi-user system.
- **Rate limiting / backoff** — no retry logic on portal errors beyond re-authentication
- **Tests** — `test_client.py` is a manual smoke test; no automated test suite

---

## What I'd improve with more time

1. **ISO 8601 timestamps** with explicit timezone in consumption responses
2. **In-memory cache** (5-min TTL) for hierarchy and export endpoints
3. **Interval delta computation** alongside cumulative readings
4. **SQLite local index** for fast filtering across all meters without hitting the portal
5. **Proper test suite** with `pytest` + `respx` (mock HTTP)
6. **Better error mapping** — portal 404 → API 404, not 502

---

## Key files

- [`PROTOCOL.md`](./PROTOCOL.md) — How the portal works: auth, endpoints, data formats, quirks
- [`REFLECTION.md`](./REFLECTION.md) — Reflection questions answered
- [`openapi.json`](./openapi.json) — OpenAPI 3.1 spec for the clean API

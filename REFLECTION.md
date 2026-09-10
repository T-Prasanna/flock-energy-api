# REFLECTION.md

## What assumptions did you make?

- **The portal is the source of truth.** I treated whatever the portal returns as correct and didn't try to validate or cross-check data (e.g. I don't verify that a meter's `dtCode` matches its hierarchy's `dt.code`).
- **The session cookie is the only auth mechanism.** I assumed no additional CSRF tokens are needed beyond the `Origin`/`Referer` headers for the login POST.
- **The HMAC timestamp window is permissive enough for a single request.** I generate the signature immediately before the request. If the portal enforces a very tight window (< a few seconds), this could fail under load. In practice, testing showed it works fine.
- **All 403 meters fit on one export page.** The export endpoint's `page` parameter exists but the portal returns all records on page 1. I assumed this holds for the current dataset and noted it as a scalability concern.
- **Energy readings are cumulative register values, not interval deltas.** The kWh values increase monotonically (~0.41 kWh per 30-min interval), which is consistent with a cumulative meter register, not interval consumption. I expose them as-is and let the consumer compute deltas if needed.
- **Timestamps are in local Indian time (IST, UTC+5:30).** The portal gives no timezone information. Jaipur is in India, so IST is the reasonable assumption.

---

## Which part was the most difficult, and how did you get unstuck?

The hardest part was finding the meter detail data. The portal has no `/portal/meters/{id}` JSON endpoint — I tried several URL patterns and got 404s. I got unstuck by remembering that SvelteKit embeds server-side load data in the HTML as a `__data.json` endpoint. Once I fetched `/meters/J100001/__data.json`, I had the data — but it was in SvelteKit's de-duplicated array format, which required writing a small resolver to turn integer references back into a nested object.

The second tricky part was the HMAC-signed export. The signing algorithm was buried in the compiled/minified JS (`nodes/4.2Bgc2kUI.js`). I read through the minified code carefully, identified the `crypto.subtle.sign` call, and reconstructed the message format: `METHOD\nPATH\nQUERY\nTIMESTAMP`. Testing confirmed it.

---

## If you had another day, what would you improve?

1. **Caching** — The export endpoint is the most expensive call (~230 KB, ~1 s). I'd add a short TTL in-memory cache (e.g. 5 minutes) for the hierarchy and export data, with a `Cache-Control` header on the API response so clients know when to re-fetch.

2. **Timestamp normalisation** — Parse the portal's `DD/MM/YYYY HH:MM` timestamps into ISO 8601 (`2026-06-23T23:30:00+05:30`) in the API response. Right now I pass them through as-is, which is a footgun for consumers.

3. **Interval consumption** — Compute per-interval kWh deltas from the cumulative register values and expose them alongside the raw readings. This is what most consumers actually want.

4. **A local index** — For the optional extension: load all 403 meters into a SQLite database on startup, enabling fast filtering by status, make, phase type, DT, feeder, etc. without hitting the portal for every query.

5. **Better error messages** — Right now a 404 from the portal becomes a generic 502. I'd map portal errors to appropriate HTTP status codes (404 → 404, auth failure → 503 with retry-after).

6. **Tests** — I have a manual `test_client.py` but no automated tests. I'd add pytest tests with `respx` to mock the portal HTTP calls.

---

## What mistake did you make while solving this?

I initially assumed the meter detail data would be available at a `/portal/meters/{id}` JSON endpoint (by analogy with the search and geo endpoints). I spent time trying variations (`/portal/meter/J100001`, `/portal/meters/J100001/detail`, etc.) before stepping back and looking at the actual network traffic pattern more carefully. The SvelteKit `__data.json` approach was the right path, and I should have checked it earlier — it's a standard SvelteKit pattern.

---

## If you were reviewing your own submission, what would you criticise?

- **The `_decode_sveltekit_data` function is fragile.** It relies on the SvelteKit serialisation format staying stable. If the portal upgrades SvelteKit, the format could change. A more robust approach would be to parse the HTML directly (the data is also in the SSR HTML) or to find a proper JSON endpoint.

- **No caching at all.** Every API call hits the portal. For a production service this is fine for low traffic, but it means the portal sees every request. A 5-minute cache on the export and hierarchy endpoints would be a significant improvement.

- **The hierarchy endpoint is expensive.** It fetches all 403 meters on every call. I noted this in the docstring but didn't fix it.

- **Credentials are hardcoded.** They're in `portal_client.py` as constants. They should be in environment variables (`.env` file). I added `python-dotenv` to requirements but didn't wire it up — a small but real omission.

- **The `test_client.py` is a manual smoke test, not a proper test suite.** There are no assertions, no mocking, and no CI.

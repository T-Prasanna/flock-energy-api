"""
Urja Meter Ops — Clean REST API wrapper.

Run with:  uvicorn main:app --reload
Docs at:   http://localhost:8000/docs
"""

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse, RedirectResponse

from portal_client import PortalClient

# ------------------------------------------------------------------ #
# App lifecycle                                                        #
# ------------------------------------------------------------------ #

_client: PortalClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    _client = PortalClient()
    yield
    await _client.aclose()


app = FastAPI(
    title="Urja Meter Ops API",
    description=(
        "A clean REST API over the Urja Meter Ops portal. "
        "Exposes meter details, consumption history, distribution transformers, "
        "and the full network hierarchy."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return JSONResponse(status_code=204, content=None)


def client() -> PortalClient:
    if _client is None:
        raise RuntimeError("Client not initialised")
    return _client


def _portal_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Portal error: {exc}")


# ------------------------------------------------------------------ #
# Meters                                                               #
# ------------------------------------------------------------------ #


@app.get(
    "/api/v1/meters",
    summary="List meters",
    tags=["Meters"],
    response_description="Paginated list of meters with basic attributes",
)
async def list_meters(
    q: Annotated[str, Query(description="Search by meter ID or serial number")] = "",
    page: Annotated[int, Query(ge=1, description="Page number (20 per page)")] = 1,
) -> dict[str, Any]:
    """
    Returns a paginated list of meters. Supports free-text search across
    meter ID and serial number. Results are 20 per page.
    """
    try:
        raw = await client().search_meters(q=q, page=page)
    except Exception as exc:
        raise _portal_error(exc)

    return {
        "data": [_normalise_meter_summary(m) for m in raw.get("data", [])],
        "total": raw.get("total", 0),
        "page": raw.get("page", page),
        "page_size": raw.get("pageSize", 20),
    }


@app.get(
    "/api/v1/meters/{meter_id}",
    summary="Get meter detail",
    tags=["Meters"],
)
async def get_meter(
    meter_id: Annotated[str, Path(description="Meter ID, e.g. J100001")],
) -> dict[str, Any]:
    """
    Returns full nameplate data, network hierarchy, and geographic coordinates
    for a single meter.
    """
    try:
        detail = await client().get_meter_detail(meter_id)
        geo_resp = await client().get_meter_geo(meter_id)
    except Exception as exc:
        raise _portal_error(exc)

    if not detail:
        raise HTTPException(status_code=404, detail="Meter not found")

    geo = geo_resp.get("data", {})
    return _normalise_meter_detail(detail, geo)


@app.get(
    "/api/v1/meters/{meter_id}/consumption",
    summary="Get meter consumption history",
    tags=["Meters"],
)
async def get_meter_consumption(
    meter_id: Annotated[str, Path(description="Meter ID, e.g. J100001")],
) -> dict[str, Any]:
    """
    Returns half-hourly energy readings for the default window (~7 days).
    Each reading includes cumulative kWh, kVAh, and R-phase voltage.
    Timestamps are in DD/MM/YYYY HH:MM format as returned by the portal.
    """
    try:
        raw = await client().get_meter_energy(meter_id)
    except Exception as exc:
        raise _portal_error(exc)

    readings = [
        {
            "timestamp": r["timestamp"],
            "kwh": _to_float(r.get("kwh")),
            "kvah": _to_float(r.get("kvah")),
            "volt_r": _to_int(r.get("voltR")),
        }
        for r in raw.get("data", [])
    ]
    return {"meter_id": meter_id, "count": len(readings), "readings": readings}


# ------------------------------------------------------------------ #
# Distribution Transformers                                            #
# ------------------------------------------------------------------ #


@app.get(
    "/api/v1/transformers",
    summary="List distribution transformers",
    tags=["Transformers"],
)
async def list_transformers(
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict[str, Any]:
    """
    Returns a paginated list of distribution transformers (DTs) with their
    feeder assignment and rated capacity.
    """
    try:
        raw = await client().list_dts(page=page)
    except Exception as exc:
        raise _portal_error(exc)

    return {
        "data": raw.get("data", []),
        "total": raw.get("total", 0),
        "page": raw.get("page", page),
        "page_size": raw.get("pageSize", 20),
    }


# ------------------------------------------------------------------ #
# Hierarchy                                                            #
# ------------------------------------------------------------------ #


@app.get(
    "/api/v1/hierarchy",
    summary="Network hierarchy tree",
    tags=["Hierarchy"],
)
async def get_hierarchy() -> dict[str, Any]:
    """
    Reconstructs the full Zone → Circle → Division → Subdivision →
    Substation → Feeder → DT hierarchy from the bulk export.

    Note: this fetches all 403 meters from the portal on first call
    (single page export). At this dataset size it completes in ~1 s.
    With millions of meters you'd want a dedicated hierarchy endpoint
    or a cached index.
    """
    try:
        raw = await client().export_meters(page=1)
    except Exception as exc:
        raise _portal_error(exc)

    return {"hierarchy": _build_hierarchy(raw.get("data", []))}


# ------------------------------------------------------------------ #
# Bulk export                                                          #
# ------------------------------------------------------------------ #


@app.get(
    "/api/v1/export",
    summary="Full meter export",
    tags=["Export"],
)
async def export_all_meters(
    page: Annotated[int, Query(ge=1, description="Export is paginated; 403 meters fit on page 1")] = 1,
) -> dict[str, Any]:
    """
    Returns the full meter dataset including nameplate, hierarchy, and
    geo coordinates. Uses the portal's HMAC-signed bulk export endpoint.
    The portal returns all 403 meters on a single page at the time of writing.
    """
    try:
        raw = await client().export_meters(page=page)
    except Exception as exc:
        raise _portal_error(exc)

    return {
        "data": [_normalise_export_record(r) for r in raw.get("data", [])],
        "total": raw.get("total", 0),
        "page": page,
    }


# ------------------------------------------------------------------ #
# Normalisation helpers                                                #
# ------------------------------------------------------------------ #


def _normalise_meter_summary(m: dict) -> dict:
    return {
        "meter_id": m.get("meterId"),
        "serial_no": m.get("serialNo"),
        "make": m.get("make"),
        "phase_type": m.get("phaseType"),
        "install_status": m.get("installStatus"),
        "dt_code": m.get("dtCode"),
    }


def _normalise_meter_detail(detail: dict, geo: dict) -> dict:
    hierarchy = detail.get("hierarchy", {})
    nameplate_raw = detail.get("detail", {})

    # The detail node can be either a list of {parameterName, parameterValue}
    # dicts or a classData blob (legacy meters). _decode_sveltekit_data
    # already resolved the references; we just need to flatten.
    nameplate: dict = {}
    if isinstance(nameplate_raw, dict) and "data" in nameplate_raw:
        for item in nameplate_raw["data"]:
            if isinstance(item, dict) and "parameterName" in item:
                nameplate[_snake(item["parameterName"])] = item["parameterValue"]

    return {
        "meter_id": detail.get("meterId"),
        "nameplate": nameplate,
        "hierarchy": {
            "zone": hierarchy.get("Zone"),
            "circle": hierarchy.get("Circle"),
            "division": hierarchy.get("Division"),
            "subdivision": hierarchy.get("Subdivision"),
            "substation": hierarchy.get("Sub Station"),
            "feeder": hierarchy.get("Feeder"),
            "dt": hierarchy.get("DT"),
        },
        "location": {
            "latitude": _to_float(geo.get("latitude")),
            "longitude": _to_float(geo.get("longitude")),
        }
        if geo
        else None,
    }


def _normalise_export_record(r: dict) -> dict:
    h = r.get("hierarchy", {})
    geo = r.get("geo", {})
    return {
        "meter_id": r.get("meterId"),
        "serial_no": r.get("serialNo"),
        "make": r.get("make"),
        "phase_type": r.get("phaseType"),
        "install_status": r.get("installStatus"),
        "install_type": r.get("installType"),
        "build": r.get("build"),
        "dt_code": r.get("dtCode"),
        "hierarchy": {
            level: {"name": v.get("name"), "code": v.get("code")}
            for level, v in h.items()
            if isinstance(v, dict)
        },
        "location": {
            "latitude": _to_float(geo.get("lat")),
            "longitude": _to_float(geo.get("lng")),
        }
        if geo
        else None,
    }


def _build_hierarchy(meters: list[dict]) -> dict:
    """Build a nested zone→circle→…→dt tree from the flat export records."""
    tree: dict = {}
    for m in meters:
        h = m.get("hierarchy", {})
        zone = _node(h, "zone")
        circle = _node(h, "circle")
        division = _node(h, "division")
        subdivision = _node(h, "subdivision")
        substation = _node(h, "substation")
        feeder = _node(h, "feeder")
        dt = _node(h, "dt")

        z = tree.setdefault(zone, {"name": h.get("zone", {}).get("name", zone), "circles": {}})
        c = z["circles"].setdefault(circle, {"name": h.get("circle", {}).get("name", circle), "divisions": {}})
        d = c["divisions"].setdefault(division, {"name": h.get("division", {}).get("name", division), "subdivisions": {}})
        s = d["subdivisions"].setdefault(subdivision, {"name": h.get("subdivision", {}).get("name", subdivision), "substations": {}})
        ss = s["substations"].setdefault(substation, {"name": h.get("substation", {}).get("name", substation), "feeders": {}})
        f = ss["feeders"].setdefault(feeder, {"name": h.get("feeder", {}).get("name", feeder), "dts": {}})
        f["dts"].setdefault(dt, {"name": h.get("dt", {}).get("name", dt), "meter_count": 0})
        f["dts"][dt]["meter_count"] += 1

    return tree


def _node(h: dict, key: str) -> str:
    v = h.get(key, {})
    return v.get("code", key) if isinstance(v, dict) else str(v)


def _snake(s: str) -> str:
    return s.lower().replace(" ", "_")


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

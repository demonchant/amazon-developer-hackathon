"""FieldCall: source-backed outdoor event forecast comparisons."""
from __future__ import annotations

import json
import asyncio
import base64
import hashlib
import os
import re
import sqlite3
import secrets
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse

CURRENT_USER: ContextVar[dict[str, str] | None] = ContextVar("fieldcall_user", default=None)
CURRENT_SESSION_COOKIE: ContextVar[str | None] = ContextVar("fieldcall_session_cookie", default=None)


def load_local_env(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE settings without overriding the process environment."""
    if not path.exists():
        return
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Malformed .env line {line_no}; expected KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"Malformed .env key on line {line_no}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '\"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_local_env()


def config() -> dict[str, Any]:
    host = os.getenv("FIELDCALL_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("FIELDCALL_PORT", "8080")))
    user_agent = os.getenv("WEATHER_USER_AGENT", "").strip()
    db_path = os.getenv("DATABASE_PATH", "data/fieldcall.sqlite3")
    timeout = float(os.getenv("WEATHER_TIMEOUT_SECONDS", "12"))
    google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    google_redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    session_signing_key = os.getenv("SESSION_SIGNING_KEY", "").strip()
    google_maps_api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if host not in ("127.0.0.1", "0.0.0.0"):
        raise ValueError("FIELDCALL_HOST must be 127.0.0.1 or 0.0.0.0")
    if not (1 <= port <= 65535):
        raise ValueError("FIELDCALL_PORT must be 1..65535")
    if not re.fullmatch(r"[^\r\n]{8,160}", user_agent):
        raise ValueError("WEATHER_USER_AGENT is required (8-160 characters; identify your app and contact)")
    if not (re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", user_agent, re.I)
            or re.search(r"https://[^\s/]+", user_agent, re.I)):
        raise ValueError("WEATHER_USER_AGENT must include a contact email or HTTPS project/contact URL")
    if not (2 <= timeout <= 60):
        raise ValueError("WEATHER_TIMEOUT_SECONDS must be 2..60")
    oauth_values = (google_client_id, google_client_secret, google_redirect_uri, session_signing_key)
    if not all(oauth_values):
        raise ValueError("Google OAuth is required: set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, and SESSION_SIGNING_KEY")
    if len(session_signing_key) < 32:
        raise ValueError("SESSION_SIGNING_KEY must contain at least 32 characters")
    if not google_redirect_uri.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("GOOGLE_REDIRECT_URI must use HTTPS except on localhost")
    return {"host": host, "port": port, "user_agent": user_agent, "db_path": db_path, "timeout": timeout,
            "google_client_id": google_client_id, "google_client_secret": google_client_secret,
            "google_redirect_uri": google_redirect_uri, "session_signing_key": session_signing_key,
            "google_maps_api_key": google_maps_api_key}


CFG = config()
Path(CFG["db_path"]).parent.mkdir(parents=True, exist_ok=True)
_met_request_lock = asyncio.Lock()
_met_last_request = 0.0


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(CFG["db_path"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


with db() as conn:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
      starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, max_temp_c REAL, min_temp_c REAL,
      max_wind_kph REAL, max_precip_mm REAL, created_at TEXT NOT NULL, owner_id TEXT
    );
    CREATE TABLE IF NOT EXISTS evaluations (
      id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
      fetched_at TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, metrics_json TEXT NOT NULL,
      alerts_json TEXT NOT NULL, forecast_url TEXT NOT NULL, alerts_url TEXT NOT NULL,
      forecast_updated TEXT, period_count INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS provider_attempts (
      provider TEXT PRIMARY KEY, attempted_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS geocode_usage (
      day TEXT PRIMARY KEY, request_count INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS forecast_cache (
      request_url TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, expires_at TEXT,
      last_modified TEXT, body_json TEXT NOT NULL
    );
    """)
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "owner_id" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN owner_id TEXT")
    if "location_address" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN location_address TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS events_owner_id_id ON events(owner_id, id DESC)")


def current_user() -> dict[str, str]:
    user = CURRENT_USER.get()
    if not user or not user.get("sub"):
        raise ValueError("Sign in with Google before using FieldCall")
    return user


def session_token(user: dict[str, str]) -> str:
    return jwt.encode({**user, "exp": datetime.now(timezone.utc) + timedelta(days=7), "iat": datetime.now(timezone.utc)},
                      CFG["session_signing_key"], algorithm="HS256")


def decode_session(token: str) -> dict[str, str] | None:
    try:
        claims = jwt.decode(token, CFG["session_signing_key"], algorithms=["HS256"], options={"require": ["sub", "exp", "iat"]})
        return {key: str(claims[key]) for key in ("sub", "email", "name") if claims.get(key)}
    except (jwt.PyJWTError, KeyError, TypeError):
        return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_event(v: dict[str, Any]) -> dict[str, Any]:
    name = str(v.get("name", "")).strip()
    if not 2 <= len(name) <= 100:
        raise ValueError("Event name must be 2–100 characters")
    lat, lon = float(v["latitude"]), float(v["longitude"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude/longitude out of range")
    location_address = str(v.get("location_address") or "").strip()
    if len(location_address) > 300 or any(ord(c) < 32 for c in location_address):
        raise ValueError("Location address is invalid")
    start = datetime.fromisoformat(str(v["starts_at"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(v["ends_at"]).replace("Z", "+00:00"))
    if not start.tzinfo or not end.tzinfo or end <= start:
        raise ValueError("Enter a valid timezone-aware event window")
    if end.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise ValueError("Event must not have ended already")
    if (end - start).total_seconds() > 72 * 3600:
        raise ValueError("Event window cannot exceed 72 hours")
    limits = {}
    for key, lo, hi in (("max_temp_c", -80, 60), ("min_temp_c", -100, 50),
                        ("max_wind_kph", 0, 500), ("max_precip_mm", 0, 500)):
        val = v.get(key)
        limits[key] = None if val in (None, "") else float(val)
        if limits[key] is not None and not lo <= limits[key] <= hi:
            raise ValueError(f"{key} is outside accepted range")
    if all(x is None for x in limits.values()):
        raise ValueError("Set at least one weather tolerance")
    if limits["min_temp_c"] is not None and limits["max_temp_c"] is not None and limits["min_temp_c"] > limits["max_temp_c"]:
        raise ValueError("Minimum temperature must not exceed maximum")
    return {"name": name, "latitude": lat, "longitude": lon, "starts_at": start.astimezone(timezone.utc).isoformat(),
            "ends_at": end.astimezone(timezone.utc).isoformat(), "location_address": location_address or None, **limits}


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_forecast(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Normalize documented Locationforecast fields; absent provider values stay absent."""
    properties = data.get("properties")
    if not isinstance(properties, dict) or not isinstance(properties.get("timeseries"), list):
        raise ValueError("MET Norway response has no properties.timeseries array")
    meta = properties.get("meta") if isinstance(properties.get("meta"), dict) else {}
    output = []
    for entry in properties["timeseries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            continue
        payload = entry["data"]
        instant = payload.get("instant", {})
        instant = instant.get("details", {}) if isinstance(instant, dict) else {}
        period_key = next((key for key in ("next_1_hours", "next_6_hours", "next_12_hours") if key in payload), None)
        period = payload.get(period_key, {}) if period_key else {}
        details = period.get("details", {}) if isinstance(period, dict) else {}
        hours = {"next_1_hours":1,"next_6_hours":6,"next_12_hours":12}.get(period_key, 1)
        output.append({"start":entry.get("time"),"hours":hours,
            "temperature":instant.get("air_temperature") if isinstance(instant,dict) else None,
            "wind_ms":instant.get("wind_speed") if isinstance(instant,dict) else None,
            "precip_mm":details.get("precipitation_amount") if isinstance(details,dict) else None})
    updated = meta.get("updated_at")
    return output, str(updated) if updated else None


def compare(event: dict[str, Any], periods: list[dict[str, Any]]) -> tuple[str, dict[str, Any], str]:
    start, end = datetime.fromisoformat(event["starts_at"]), datetime.fromisoformat(event["ends_at"])
    selected = []
    intervals = []
    for p in periods:
        try:
            ps = parse_dt(p["start"])
            pe = ps + timedelta(hours=int(p["hours"]))
            if ps < end and pe > start:
                selected.append(p)
                intervals.append((ps, pe))
        except (KeyError, ValueError, TypeError):
            continue
    if not selected:
        return "unknown", {"periods": 0}, "No hourly forecast periods overlap this event window."
    cursor = start
    for ps, pe in sorted(intervals):
        if pe <= cursor: continue
        if ps > cursor: break
        cursor = max(cursor, pe)
    coverage_complete = cursor >= end
    temps = [float(p["temperature"]) if p.get("temperature") is not None else None for p in selected]
    winds = [float(p["wind_ms"]) * 3.6 if p.get("wind_ms") is not None else None for p in selected]
    rain = []
    for p in selected:
        # Period precipitation is an aggregate over the whole provider interval.
        # Do not prorate or attribute it to a partial overlap with the event.
        ps=parse_dt(p["start"]); pe=ps+timedelta(hours=int(p["hours"]))
        fully_inside=ps >= start and pe <= end
        rain.append(float(p["precip_mm"]) if fully_inside and p.get("precip_mm") is not None else None)
    observed = {
        "periods": len(selected),
        "coverage_complete": coverage_complete,
        "min_temp_c": min((x for x in temps if x is not None), default=None),
        "max_temp_c": max((x for x in temps if x is not None), default=None),
        "max_wind_kph": max((x for x in winds if x is not None), default=None),
        "max_precip_mm": max((x for x in rain if x is not None), default=None),
    }
    checks = []
    for limit, values, actual, condition in (
          (event.get("max_temp_c"), temps, observed["max_temp_c"], lambda a,b:a>b),
          (event.get("min_temp_c"), temps, observed["min_temp_c"], lambda a,b:a<b),
          (event.get("max_wind_kph"), winds, observed["max_wind_kph"], lambda a,b:a>b)):
        if limit is not None:
            checks.append(None if not coverage_complete or actual is None or any(x is None for x in values) else condition(actual, limit))
    if event.get("max_precip_mm") is not None:
        actual=observed["max_precip_mm"]
        checks.append(None if not coverage_complete or actual is None or any(x is None for x in rain) else actual > event["max_precip_mm"])
    if any(x is True for x in checks):
        status, summary = "outside your limits", "At least one available forecast measurement exceeds a saved tolerance."
    elif checks and all(x is False for x in checks):
        status, summary = "within your limits", "All configured and measurable tolerances are within your saved limits."
    else:
        status, summary = "unknown", "Forecast coverage or measurements are incomplete for one or more saved tolerances."
    return status, observed, summary


async def met_forecast(client: httpx.AsyncClient, url: str) -> tuple[dict[str, Any], str, bool]:
    """Use MET Norway expiry and conditional caching; cached observations retain original fetch time."""
    with db() as conn:
        cache=conn.execute("SELECT * FROM forecast_cache WHERE request_url=?",(url,)).fetchone()
    current=datetime.now(timezone.utc)
    if cache and cache["expires_at"] and parse_dt(cache["expires_at"]) > current:
        return json.loads(cache["body_json"]), cache["fetched_at"], True
    headers={"User-Agent":CFG["user_agent"],"Accept":"application/json","Accept-Encoding":"gzip, deflate"}
    global _met_last_request
    async with _met_request_lock:
        # Another concurrent request may have populated this coordinate while we waited.
        with db() as conn:
            cache=conn.execute("SELECT * FROM forecast_cache WHERE request_url=?",(url,)).fetchone()
        current=datetime.now(timezone.utc)
        if cache and cache["expires_at"] and parse_dt(cache["expires_at"]) > current:
            return json.loads(cache["body_json"]),cache["fetched_at"],True
        if cache and cache["last_modified"]: headers["If-Modified-Since"]=cache["last_modified"]
        loop=asyncio.get_running_loop()
        delay=0.1-(loop.time()-_met_last_request)
        if delay > 0: await asyncio.sleep(delay)
        _met_last_request=loop.time()
        response=await client.get(url,headers=headers)
    if response.status_code == 304 and cache:
        data=json.loads(cache["body_json"]); fetched=cache["fetched_at"]
    else:
        response.raise_for_status(); data=response.json(); fetched=now()
        if not isinstance(data,dict): raise ValueError("MET Norway returned an unexpected document")
    expires=response.headers.get("Expires") or (cache["expires_at"] if cache else None)
    if not expires:
        raise ValueError("MET Norway response has no Expires header; refusing an uncached repeatable forecast")
    try:
        parse_dt(expires)
    except ValueError:
        from email.utils import parsedate_to_datetime
        expires_dt=parsedate_to_datetime(expires)
        expires=expires_dt.astimezone(timezone.utc).isoformat()
    last_modified=response.headers.get("Last-Modified") or (cache["last_modified"] if cache else None)
    with db() as conn:
        conn.execute("INSERT INTO forecast_cache(request_url,fetched_at,expires_at,last_modified,body_json) VALUES(?,?,?,?,?) ON CONFLICT(request_url) DO UPDATE SET fetched_at=excluded.fetched_at,expires_at=excluded.expires_at,last_modified=excluded.last_modified,body_json=excluded.body_json",
          (url,fetched,expires,last_modified,json.dumps(data,separators=(",",":"))))
    return data,fetched,False


async def evaluate(event_id: int) -> dict[str, Any]:
    owner_id = current_user()["sub"]
    with db() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=? AND owner_id=?", (event_id, owner_id)).fetchone()
    if not row: raise ValueError("Event not found")
    event = dict(row)
    timeout = httpx.Timeout(CFG["timeout"])
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        lat=f"{event['latitude']:.4f}"; lon=f"{event['longitude']:.4f}"
        forecast_url="https://api.met.no/weatherapi/locationforecast/2.0/compact?"+urlencode({"lat":lat,"lon":lon})
        forecast,fetched,cached=await met_forecast(client,forecast_url)
        periods,updated=normalize_forecast(forecast)
        status,metrics,summary=compare(event,periods)
        metrics["source_cached"]=cached
        alerts_url=""
        alert_state={"available":False,"reason":"MET Norway Locationforecast does not provide event alert lookup in this integration."}
    with db() as conn:
        cur = conn.execute("INSERT INTO evaluations(event_id,fetched_at,status,summary,metrics_json,alerts_json,forecast_url,alerts_url,forecast_updated,period_count) VALUES(?,?,?,?,?,?,?,?,?,?)",
          (event_id, fetched, status, summary, json.dumps(metrics), json.dumps(alert_state), forecast_url, alerts_url, updated, int(metrics.get("periods", 0))))
        eval_id = cur.lastrowid
        saved = conn.execute("SELECT * FROM evaluations WHERE id=?", (eval_id,)).fetchone()
    return evaluation_dict(saved)


def evaluation_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    v = dict(row)
    for key, out in (("metrics_json", "metrics"), ("alerts_json", "alerts")):
        v[out] = json.loads(v.pop(key))
    return v


def event_dict(row: sqlite3.Row, history: bool = True) -> dict[str, Any]:
    item = dict(row)
    item.pop("owner_id", None)
    if history:
        with db() as conn:
            item["evaluations"] = [evaluation_dict(x) for x in conn.execute("SELECT * FROM evaluations WHERE event_id=? ORDER BY id DESC", (item["id"],))]
    return item


mcp = FastMCP("FieldCall", instructions="Save outdoor events worldwide and compare user-entered weather tolerances with MET Norway global forecasts. Results are not safety advice.",
    host=CFG["host"], port=CFG["port"], streamable_http_path="/mcp", json_response=True, stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
      allowed_hosts=((["127.0.0.1:*", "localhost:*", "web:*"] if CFG["host"] == "0.0.0.0" else ["127.0.0.1:*", "localhost:*"])
        + ([f"{os.environ['RENDER_EXTERNAL_HOSTNAME']}:*"] if os.getenv("RENDER_EXTERNAL_HOSTNAME") else [])),
      allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]
        + ([f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}"] if os.getenv("RENDER_EXTERNAL_HOSTNAME") else [])))


@mcp.tool()
def save_outdoor_event(name: str, latitude: float, longitude: float, starts_at: str, ends_at: str,
                       max_temp_c: float | None = None, min_temp_c: float | None = None,
                       max_wind_kph: float | None = None, max_precip_mm: float | None = None,
                       location_address: str | None = None) -> dict[str, Any]:
    """Persist a user-defined outdoor event and its own weather tolerances; no weather is fabricated."""
    value = validate_event(locals())
    owner_id = current_user()["sub"]
    with db() as conn:
        cur = conn.execute("INSERT INTO events(name,latitude,longitude,starts_at,ends_at,max_temp_c,min_temp_c,max_wind_kph,max_precip_mm,created_at,owner_id,location_address) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
          (value["name"], value["latitude"], value["longitude"], value["starts_at"], value["ends_at"], value["max_temp_c"], value["min_temp_c"], value["max_wind_kph"], value["max_precip_mm"], now(), owner_id, value["location_address"]))
        row = conn.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()
    return event_dict(row)


@mcp.tool()
async def recheck_outdoor_event(event_id: int) -> dict[str, Any]:
    """Fetch a current MET Norway Locationforecast, compare configured limits, and persist a recheck."""
    try:
        return await evaluate(event_id)
    except ValueError as exc:
        if str(exc) == "Event not found": raise
        raise ValueError(f"Live MET Norway result unavailable: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"Live MET Norway request failed: {type(exc).__name__}: {exc}") from exc


@mcp.tool()
def list_outdoor_events() -> list[dict[str, Any]]:
    """List only events actually saved by this user, with persisted evaluation history."""
    with db() as conn: rows = conn.execute("SELECT * FROM events WHERE owner_id=? ORDER BY id DESC", (current_user()["sub"],)).fetchall()
    return [event_dict(r) for r in rows]


STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
DASHBOARD = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
PRIVACY = (STATIC_DIR / "privacy.html").read_text(encoding="utf-8")
TERMS = (STATIC_DIR / "terms.html").read_text(encoding="utf-8")
STYLE = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
DASHBOARD_STYLE = (STATIC_DIR / "dashboard.css").read_text(encoding="utf-8")
LANDING_STYLE = (STATIC_DIR / "landing.css").read_text(encoding="utf-8")
SCRIPT = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
HERO_IMAGE = (STATIC_DIR / "fieldcall-hero.webp").read_bytes()
DASHBOARD_IMAGE = (STATIC_DIR / "dashboard-bg.webp").read_bytes()


async def homepage(request: Request): return HTMLResponse(INDEX)
async def dashboard(request: Request): return HTMLResponse(DASHBOARD)
async def privacy_page(request: Request): return HTMLResponse(PRIVACY)
async def terms_page(request: Request): return HTMLResponse(TERMS)
async def stylesheet(request: Request):
    from starlette.responses import Response
    return Response(STYLE, media_type="text/css")
async def landing_stylesheet(request: Request):
    from starlette.responses import Response
    return Response(LANDING_STYLE, media_type="text/css")
async def dashboard_stylesheet(request: Request):
    from starlette.responses import Response
    return Response(DASHBOARD_STYLE, media_type="text/css")
async def hero_image(request: Request):
    from starlette.responses import Response
    return Response(HERO_IMAGE, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})
async def dashboard_image(request: Request):
    from starlette.responses import Response
    return Response(DASHBOARD_IMAGE, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})
async def javascript(request: Request):
    from starlette.responses import Response
    return Response(SCRIPT, media_type="text/javascript")


def normalize_geocode_results(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate real Google Geocoding results before exposing location choices."""
    if document.get("status") != "OK":
        if document.get("status") == "ZERO_RESULTS":
            return []
        raise ValueError("Google Maps could not resolve this location")
    raw_results = document.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("Google Maps returned an invalid result")
    output = []
    for item in raw_results[:5]:
        if not isinstance(item, dict) or not isinstance(item.get("formatted_address"), str):
            continue
        geometry = item.get("geometry")
        point = geometry.get("location") if isinstance(geometry, dict) else None
        if not isinstance(point, dict):
            continue
        try:
            lat, lon = float(point["lat"]), float(point["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            output.append({"address": item["formatted_address"], "latitude": lat, "longitude": lon})
    return output


async def api_geocode(request: Request):
    if not CFG["google_maps_api_key"]:
        return JSONResponse({"error": "Address lookup is not configured yet. Enter coordinates manually or configure Google Maps Geocoding."}, status_code=503)
    try:
        value = await bounded_json(request)
        address = str(value.get("address", "")).strip()
        if not 3 <= len(address) <= 250 or any(ord(c) < 32 for c in address):
            raise ValueError("Enter a location between 3 and 250 characters")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # Hard application cap: at most 100 Google geocoding calls per UTC day.
    # Also set the matching per-day quota in Google Cloud Console.
    day = datetime.now(timezone.utc).date().isoformat()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT request_count FROM geocode_usage WHERE day=?", (day,)).fetchone()
        if row and row["request_count"] >= 100:
            conn.rollback()
            return JSONResponse({"error": "Address lookup daily limit reached. Try again tomorrow or enter coordinates manually."}, status_code=429)
        conn.execute("INSERT INTO geocode_usage(day,request_count) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET request_count=request_count+1", (day,))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CFG["timeout"]), follow_redirects=False) as client:
            response = await client.get("https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": address, "key": CFG["google_maps_api_key"]})
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise ValueError("Google Maps returned an invalid response")
        results = normalize_geocode_results(document)
        if not results:
            return JSONResponse({"results": [], "message": "Google Maps found no matching locations. Try adding a city or country."})
        return JSONResponse({"results": results, "attribution": "Google Maps"})
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return JSONResponse({"error": f"Address lookup failed: {type(exc).__name__}. You can enter coordinates manually."}, status_code=502)


async def api_events(request: Request):
    if request.method == "GET":
        try: return JSONResponse(await invoke_mcp("list_outdoor_events", {}))
        except Exception as exc: return JSONResponse({"error":f"MCP event list unavailable: {type(exc).__name__}"},status_code=502)
    try:
        value = validate_event(await bounded_json(request))
        event = await invoke_mcp("save_outdoor_event", value)
        return JSONResponse(event, status_code=201)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc: return JSONResponse({"error":str(exc)},status_code=400)
async def api_recheck(request: Request):
    try: return JSONResponse(await invoke_mcp("recheck_outdoor_event", {"event_id":int(request.path_params["event_id"])}))
    except ToolFailure as exc:
        detail = str(exc)
        code = 404 if "event not found" in detail.lower() else 502 if "live met" in detail.lower() else 400
        return JSONResponse({"error":detail},status_code=code)
    except ValueError as exc: return JSONResponse({"error":str(exc)},status_code=400)
    except (httpx.HTTPError, json.JSONDecodeError) as exc: return JSONResponse({"error":f"Live MET Norway request failed: {exc.__class__.__name__}: {exc}"},status_code=502)
    except Exception as exc: return JSONResponse({"error":f"MET Norway response could not be processed: {exc.__class__.__name__}"},status_code=502)


async def api_assistant(request: Request):
    try:
        value = await bounded_json(request)
        message = str(value.get("message", "")).strip()
        if not 1 <= len(message) <= 300: raise ValueError("Request must be 1–300 characters")
        normalized = message.lower()
        if re.search(r"\b(?:list|show|view|what are)\b.*\b(event|plan|note)s?\b|\bmy (?:saved )?events\b", normalized):
            rows = await invoke_mcp("list_outdoor_events", {})
            if not isinstance(rows, list): raise ValueError("MCP list response had an invalid shape")
            if not rows: reply = "You have no saved events yet. Save a real event in the form below, then ask again."
            else:
                latest = [(r.get("name", "(unnamed event)"), r.get("id"), r.get("evaluations", [])) for r in rows]
                reply = "Saved events: " + "; ".join(f"{name} (event {eid}, {checks[0]['status'] if checks else 'not checked'})" for name,eid,checks in latest)
            return JSONResponse({"tool":"list_outdoor_events","reply":reply})
        match = re.search(r"\b(?:recheck|check|refresh)\s+(?:event\s*)?#?(\d+)\b", normalized)
        if match:
            result = await invoke_mcp("recheck_outdoor_event", {"event_id":int(match.group(1))})
            return JSONResponse({"tool":"recheck_outdoor_event","reply":f"Event {result['event_id']} check: {result['status']}. {result['summary']} Fetched {result['fetched_at']}."})
        return JSONResponse({"tool":None,"reply":"I can list saved events or recheck a saved event by its ID. For example: “List my events” or “Recheck event followed by an ID from your saved list”. Save a new event in the form below."})
    except ToolFailure as exc:
        detail = str(exc)
        code = 404 if "event not found" in detail.lower() else 502 if "live met" in detail.lower() else 400
        return JSONResponse({"error":detail},status_code=code)
    except (ValueError, KeyError, TypeError) as exc:
        return JSONResponse({"error":str(exc)},status_code=400)


async def bounded_json(request: Request) -> dict[str, Any]:
    length = request.headers.get("content-length")
    if length and (not length.isdecimal() or int(length) > 65536):
        raise ValueError("Request body exceeds 64 KiB")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 65536: raise ValueError("Request body exceeds 64 KiB")
    value = json.loads(chunks)
    if not isinstance(value, dict): raise ValueError("Expected a JSON object")
    return value


async def invoke_mcp(tool: str, arguments: dict[str, Any]) -> Any:
    """Browser client bridge uses the actual local Streamable HTTP MCP endpoint."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    failure = None
    payload = None
    headers = {}
    session_cookie = CURRENT_SESSION_COOKIE.get()
    if session_cookie:
        headers["Cookie"] = f"fieldcall_session={session_cookie}"
    async with streamablehttp_client(f"http://127.0.0.1:{CFG['port']}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            if result.isError:
                failure = result.content[0].text if result.content and hasattr(result.content[0], "text") else "MCP tool returned an error"
                failure = failure.removeprefix(f"Error executing tool {tool}: ")
            elif result.structuredContent is not None:
                payload = result.structuredContent
                if isinstance(payload, dict) and set(payload) == {"result"}: payload = payload["result"]
            elif result.content and hasattr(result.content[0], "text"):
                payload = json.loads(result.content[0].text)
            else:
                failure = "MCP tool returned no structured result"
    # Raise after leaving the SDK task group; exceptions inside its context are
    # otherwise wrapped as ExceptionGroup and lose the useful error category.
    if failure is not None: raise ToolFailure(failure)
    return payload


class ToolFailure(ValueError):
    """An actual MCP tool failure, preserved for honest HTTP/browser state."""
async def health(request: Request):
    try:
        with db() as conn: conn.execute("SELECT 1").fetchone()
        return JSONResponse({"status":"ok","database":"reachable","met_norway":"not checked by health probe","mcp":"/mcp"})
    except sqlite3.Error:
        return JSONResponse({"status":"unavailable","database":"unavailable"}, status_code=503)


async def auth_login(request: Request):
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode("ascii")
    query = urlencode({"client_id": CFG["google_client_id"], "redirect_uri": CFG["google_redirect_uri"],
        "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": code_challenge, "code_challenge_method": "S256", "prompt": "select_account"})
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + query, status_code=302)
    secure = CFG["google_redirect_uri"].startswith("https://")
    for name, value in (("fieldcall_oauth_state", state), ("fieldcall_oauth_nonce", nonce), ("fieldcall_oauth_verifier", verifier)):
        response.set_cookie(name, value, max_age=600, httponly=True, secure=secure, samesite="lax", path="/auth")
    return response


async def auth_callback(request: Request):
    state = request.query_params.get("state", "")
    expected_state = request.cookies.get("fieldcall_oauth_state", "")
    expected_nonce = request.cookies.get("fieldcall_oauth_nonce", "")
    verifier = request.cookies.get("fieldcall_oauth_verifier", "")
    if not state or not expected_state or not secrets.compare_digest(state, expected_state) or not expected_nonce or not verifier:
        return PlainTextResponse("Google sign-in could not be verified. Please try again.", status_code=400)
    if request.query_params.get("error"):
        return PlainTextResponse("Google sign-in was cancelled or denied. Please try again.", status_code=400)
    code = request.query_params.get("code", "")
    if not code:
        return PlainTextResponse("Google did not return an authorization code.", status_code=400)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        token_response = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": CFG["google_client_id"], "client_secret": CFG["google_client_secret"],
            "redirect_uri": CFG["google_redirect_uri"], "grant_type": "authorization_code", "code_verifier": verifier})
        if token_response.status_code != 200:
            return PlainTextResponse("Google sign-in failed while exchanging its authorization code.", status_code=502)
        body = token_response.json()
        identity_token = body.get("id_token") if isinstance(body, dict) else None
        if not isinstance(identity_token, str):
            return PlainTextResponse("Google did not return an identity token.", status_code=502)
        key_response = await client.get("https://www.googleapis.com/oauth2/v3/certs")
        if key_response.status_code != 200:
            return PlainTextResponse("Google identity keys are temporarily unavailable.", status_code=502)
        keys = key_response.json().get("keys", [])
    try:
        header = jwt.get_unverified_header(identity_token)
        jwk = next(key for key in keys if key.get("kid") == header.get("kid") and key.get("kty") == "RSA")
        claims = jwt.decode(identity_token, jwt.PyJWK.from_dict(jwk).key, algorithms=["RS256"],
            audience=CFG["google_client_id"], issuer=["https://accounts.google.com", "accounts.google.com"],
            options={"require": ["exp", "iat", "sub", "aud", "iss", "nonce"]})
        if abs(time.time() - int(claims["iat"])) > 600:
            raise jwt.InvalidTokenError("identity token is too old")
        if not secrets.compare_digest(str(claims["nonce"]), expected_nonce):
            raise jwt.InvalidTokenError("nonce mismatch")
        if claims.get("azp") and claims["azp"] != CFG["google_client_id"]:
            raise jwt.InvalidTokenError("authorized party mismatch")
        if claims.get("email_verified") is not True or not claims.get("email"):
            raise jwt.InvalidTokenError("verified email required")
    except (jwt.PyJWTError, KeyError, StopIteration, TypeError):
        return PlainTextResponse("Google identity could not be verified. Please try again.", status_code=401)
    user = {"sub": str(claims["sub"]), "email": str(claims["email"]), "name": str(claims.get("name") or claims["email"])}
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("fieldcall_session", session_token(user), max_age=7 * 24 * 3600, httponly=True,
        secure=CFG["google_redirect_uri"].startswith("https://"), samesite="lax", path="/")
    response.delete_cookie("fieldcall_oauth_state", path="/auth")
    response.delete_cookie("fieldcall_oauth_nonce", path="/auth")
    response.delete_cookie("fieldcall_oauth_verifier", path="/auth")
    return response


async def auth_logout(request: Request):
    response = JSONResponse({"signed_out": True})
    response.delete_cookie("fieldcall_session", path="/")
    return response


async def api_session(request: Request):
    user = CURRENT_USER.get()
    if not user:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, "name": user.get("name", ""), "email": user.get("email", "")})


for path, endpoint, methods in [("/",homepage,["GET"]),("/privacy",privacy_page,["GET"]),("/terms",terms_page,["GET"]),("/dashboard",dashboard,["GET"]),("/landing.css",landing_stylesheet,["GET"]),("/dashboard.css",dashboard_stylesheet,["GET"]),("/fieldcall-hero.webp",hero_image,["GET"]),("/dashboard-bg.webp",dashboard_image,["GET"]),("/style.css",stylesheet,["GET"]),("/app.js",javascript,["GET"]),("/api/session",api_session,["GET"]),("/api/geocode",api_geocode,["POST"]),("/api/events",api_events,["GET","POST"]),("/api/events/{event_id:int}/recheck",api_recheck,["POST"]),("/api/assistant",api_assistant,["POST"]),("/health",health,["GET"]),("/auth/login",auth_login,["GET"]),("/auth/callback",auth_callback,["GET"]),("/auth/logout",auth_logout,["POST"])]:
    mcp.custom_route(path, methods=methods)(endpoint)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


class AccessControl(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        public_paths = {"/health", "/", "/privacy", "/terms", "/style.css", "/landing.css", "/dashboard.css", "/dashboard-bg.webp", "/fieldcall-hero.webp", "/app.js", "/api/session", "/auth/login", "/auth/callback", "/auth/logout"}
        user = decode_session(request.cookies.get("fieldcall_session", ""))
        if path == "/dashboard" and not user:
            return RedirectResponse("/auth/login", status_code=303)
        if path not in public_paths and not user:
            return JSONResponse({"error": "Sign in with Google to continue", "login": "/auth/login"}, status_code=401)
        user_token = CURRENT_USER.set(user)
        cookie_token = CURRENT_SESSION_COOKIE.set(request.cookies.get("fieldcall_session"))
        try:
            return await call_next(request)
        finally:
            CURRENT_USER.reset(user_token)
            CURRENT_SESSION_COOKIE.reset(cookie_token)


if __name__ == "__main__":
    server = mcp.streamable_http_app()
    server.add_middleware(AccessControl)
    server.add_middleware(SecurityHeaders)
    uvicorn.run(server, host=CFG["host"], port=CFG["port"], log_level="info")

# flying_tools.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

import requests
import folium
from google.cloud import storage


OpenSkyWeather = Literal["good", "bad"]

OPENSKY_API_BASE = "https://opensky-network.org/api"
OPENSKY_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"


# -------------------------------------------------------------------
# Config loaders (supports package-run and script-run)
# -------------------------------------------------------------------
def _load_backend_config() -> Tuple[str, str, str, str]:
    """
    Loads:
      - OPENSKY_CLIENT_ID
      - OPENSKY_CLIENT_SECRET
      - MAPS_GCS_BUCKET
      - MAPS_GCS_FOLDER
    from config.py.

    Works for both:
      - python -m package.module (relative import)
      - python file.py (absolute import)
    """
    try:
        from ..config import (  # type: ignore
            OPENSKY_CLIENT_ID,
            OPENSKY_CLIENT_SECRET,
            MAPS_GCS_BUCKET,
            MAPS_GCS_FOLDER,
        )
    except Exception:
        from ..config import (  # type: ignore
            OPENSKY_CLIENT_ID,
            OPENSKY_CLIENT_SECRET,
            MAPS_GCS_BUCKET,
            MAPS_GCS_FOLDER,
        )

    return OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET, MAPS_GCS_BUCKET, MAPS_GCS_FOLDER


def _upload_html_to_gcs(
    local_path: str,
    bucket_name: str,
    folder: str,
    dest_filename: str,
) -> Dict[str, str]:
    """
    Upload local HTML file to GCS and return identifiers.
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    folder = folder.strip("/")
    object_name = f"{folder}/{dest_filename}" if folder else dest_filename

    blob = bucket.blob(object_name)
    blob.content_type = "text/html"
    blob.cache_control = "no-store"
    blob.upload_from_filename(local_path)

    return {
        "gcs_bucket": bucket_name,
        "gcs_object": object_name,
        "gcs_uri": f"gs://{bucket_name}/{object_name}",
        # only usable if object/bucket is public; otherwise Chainlit should generate a signed URL
        "public_url": f"https://storage.googleapis.com/{bucket_name}/{object_name}",
    }


# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------
def _chunk_time_range(begin_ts: int, end_ts: int, chunk_seconds: int = 6 * 3600) -> Iterable[Tuple[int, int]]:
    """Yield (b, e) chunks to avoid too-large time ranges."""
    b = begin_ts
    while b < end_ts:
        e = min(b + chunk_seconds, end_ts)
        yield b, e
        b = e


def _opensky_get_token(client_id: str, client_secret: str) -> str:
    """OAuth2 Client Credentials flow for OpenSky."""
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    r = requests.post(OPENSKY_TOKEN_URL, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _opensky_get(endpoint: str, token: str, params: Dict[str, Any]) -> Optional[Any]:
    """Generic OpenSky GET wrapper. Returns None for 404 (no data)."""
    url = f"{OPENSKY_API_BASE}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _get_departures(token: str, dep_airport_icao: str, begin_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    """Get flights departing from an airport in a time range using /flights/departure."""
    data = _opensky_get(
        endpoint="/flights/departure",
        token=token,
        params={"airport": dep_airport_icao, "begin": begin_ts, "end": end_ts},
    )
    return data or []


def _find_recent_flights(
    token: str,
    dep_icao: str,
    arr_icao: str,
    lookback_hours: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """Fetch multiple recent flights from dep_icao to arr_icao within a lookback window."""
    end = int(datetime.now(timezone.utc).timestamp())
    begin = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp())

    flights: List[Dict[str, Any]] = []
    for b, e in _chunk_time_range(begin, end, chunk_seconds=6 * 3600):
        flights.extend(_get_departures(token, dep_icao, b, e))

    candidates = [f for f in flights if f.get("estArrivalAirport") == arr_icao]
    candidates.sort(key=lambda x: x.get("firstSeen", 0), reverse=True)

    # Deduplicate by (icao24, firstSeen)
    seen = set()
    unique: List[Dict[str, Any]] = []
    for f in candidates:
        k = (f.get("icao24"), f.get("firstSeen"))
        if k in seen:
            continue
        seen.add(k)
        unique.append(f)

    return unique[:limit]


def _get_track_for_flight(token: str, icao24: str, first_seen_time: int) -> Optional[Dict[str, Any]]:
    """
    Get a trajectory/track for an aircraft at a given time.
    Tries /tracks/all first, then falls back to /tracks.
    """
    t = _opensky_get(
        endpoint="/tracks/all",
        token=token,
        params={"icao24": icao24.lower(), "time": first_seen_time},
    )
    if isinstance(t, dict) and t.get("path"):
        return t

    t2 = _opensky_get(
        endpoint="/tracks",
        token=token,
        params={"icao24": icao24.lower(), "time": first_seen_time},
    )
    if isinstance(t2, dict) and t2.get("path"):
        return t2

    return None


def _build_map_with_multiple_flight_tracks(
    dep_icao: str,
    arr_icao: str,
    tracks: List[Dict[str, Any]],
    out_html: str,
) -> Optional[str]:
    """Plot multiple tracks as separate toggleable layers (FeatureGroups)."""
    colors = ["purple", "red", "blue", "green", "orange", "black", "brown", "cadetblue"]

    all_points: List[Tuple[float, float]] = []
    processed: List[Tuple[Dict[str, Any], List[Tuple[float, float]]]] = []

    for t in tracks:
        path = t.get("path", [])
        pts = [(p[1], p[2]) for p in path if p and len(p) >= 3 and p[1] is not None and p[2] is not None]
        if len(pts) >= 2:
            all_points.extend(pts)
            processed.append((t, pts))

    if len(all_points) < 2:
        return None

    mid_lat = sum(p[0] for p in all_points) / len(all_points)
    mid_lng = sum(p[1] for p in all_points) / len(all_points)

    m = folium.Map(location=[mid_lat, mid_lng], zoom_start=3, tiles="OpenStreetMap")
    title_fg = folium.FeatureGroup(name=f"All tracks {dep_icao} → {arr_icao}", show=True)
    title_fg.add_to(m)

    for idx, (t, pts) in enumerate(processed):
        callsign = (t.get("callsign") or f"flight_{idx+1}").strip()
        start_time = t.get("startTime")
        end_time = t.get("endTime")

        layer_name = f"{idx+1}) {callsign} [{start_time}→{end_time}]"
        fg = folium.FeatureGroup(name=layer_name, show=(idx == 0))

        color = colors[idx % len(colors)]
        folium.PolyLine(pts, color=color, weight=5, opacity=0.85, tooltip=callsign).add_to(fg)
        folium.Marker(pts[0], popup=f"{callsign} start").add_to(fg)
        folium.Marker(pts[-1], popup=f"{callsign} end").add_to(fg)
        fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds(all_points)
    m.save(out_html)
    return out_html


# -------------------------------------------------------------------
# Tool function
# -------------------------------------------------------------------
def flying_tracks_map(
    dep_icao: str,
    arr_icao: str,
    weather: OpenSkyWeather = "good",
    n_routes: Optional[int] = None,
    lookback_hours: Optional[int] = None,
    out_html: str = "route_map.html",
) -> Dict[str, Any]:
    """
    Fetch observed flight tracks from OpenSky and save them as an interactive HTML map,
    then upload the HTML to GCS bucket/folder from config.py.

    Returns:
      - status
      - out_html (local filename)
      - gcs_bucket, gcs_object, gcs_uri, public_url (upload results)
      - other metadata about tracks
    """
    try:
        dep = dep_icao.strip().upper()
        arr = arr_icao.strip().upper()
        w = weather.strip().lower()
        if w not in ("good", "bad"):
            w = "good"

        # Defaults based on weather (your original logic)
        if n_routes is None:
            n_routes = 5 if w == "bad" else 1
        if lookback_hours is None:
            lookback_hours = 48 if w == "bad" else 24

        opensky_client_id, opensky_client_secret, maps_bucket, maps_folder = _load_backend_config()

        if not opensky_client_id or not opensky_client_secret:
            return {
                "status": "error",
                "error_message": "Missing OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET in config.py",
            }

        if not maps_bucket:
            return {"status": "error", "error_message": "Missing MAPS_GCS_BUCKET in config.py"}
        if maps_folder is None:
            return {"status": "error", "error_message": "Missing MAPS_GCS_FOLDER in config.py"}

        token = _opensky_get_token(opensky_client_id, opensky_client_secret)

        # Find flights
        flights = _find_recent_flights(token, dep, arr, lookback_hours=lookback_hours, limit=n_routes * 2)
        if not flights:
            return {
                "status": "error",
                "error_message": f"No flights found for {dep}→{arr} in last {lookback_hours} hours.",
                "dep_icao": dep,
                "arr_icao": arr,
                "lookback_hours": lookback_hours,
            }

        tracks: List[Dict[str, Any]] = []
        skipped = 0

        for f in flights:
            if len(tracks) >= n_routes:
                break

            icao24 = f.get("icao24")
            first_seen = f.get("firstSeen")
            if not icao24 or not first_seen:
                skipped += 1
                continue

            t = _get_track_for_flight(token, icao24, int(first_seen))
            if t and t.get("path"):
                tracks.append(t)
            else:
                skipped += 1

        if not tracks:
            return {
                "status": "error",
                "error_message": "Flights found but no track data available (tracks can be missing/experimental).",
                "dep_icao": dep,
                "arr_icao": arr,
                "lookback_hours": lookback_hours,
                "flights_considered": len(flights),
                "skipped": skipped,
            }

        out = _build_map_with_multiple_flight_tracks(dep, arr, tracks, out_html=out_html)
        if not out:
            return {"status": "error", "error_message": "Could not build map from track points."}

        # Upload to GCS
        gcs_info = _upload_html_to_gcs(
            local_path=out_html,
            bucket_name=maps_bucket,
            folder=maps_folder,
            dest_filename=out_html,
        )

        return {
            "status": "success",
            "dep_icao": dep,
            "arr_icao": arr,
            "weather": w,
            "requested_routes": n_routes,
            "lookback_hours": lookback_hours,
            "tracks_found": len(tracks),
            "flights_considered": len(flights),
            "skipped": skipped,
            "out_html": out,
            **gcs_info,
            "warning": "OpenSky provides observed tracking data, not planned routes or schedules.",
            "note": "If bucket is private, Chainlit should generate a signed URL for gcs_object and iframe it.",
        }

    except Exception as e:
        return {"status": "error", "error_message": str(e)}
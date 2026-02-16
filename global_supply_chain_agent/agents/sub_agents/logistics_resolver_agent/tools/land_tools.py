# land_tools.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

import folium
import googlemaps
import polyline
from google.cloud import storage


LandMode = Literal["driving", "transit", "walking", "bicycling"]


def _load_backend_config() -> Tuple[str, str, str]:
    """
    Load required config values from config.py.
    Works both as package import and script import.
    """
    try:
        from .config import GOOGLE_MAPS_API_KEY, MAPS_GCS_BUCKET, MAPS_GCS_FOLDER  # type: ignore
    except Exception:
        from config import GOOGLE_MAPS_API_KEY, MAPS_GCS_BUCKET, MAPS_GCS_FOLDER  # type: ignore

    return GOOGLE_MAPS_API_KEY, MAPS_GCS_BUCKET, MAPS_GCS_FOLDER


def _upload_html_to_gcs(
    local_path: str,
    bucket_name: str,
    folder: str,
    dest_filename: str,
) -> Dict[str, str]:
    """
    Upload local HTML file to GCS (text/html). Returns GCS identifiers.
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    folder = folder.strip("/")

    # object path within bucket
    object_name = f"{folder}/{dest_filename}" if folder else dest_filename

    blob = bucket.blob(object_name)

    # Set metadata for browser-friendly rendering
    blob.content_type = "text/html"
    blob.cache_control = "no-store"

    blob.upload_from_filename(local_path)

    return {
        "gcs_bucket": bucket_name,
        "gcs_object": object_name,
        "gcs_uri": f"gs://{bucket_name}/{object_name}",
        # only works if object/bucket is public; otherwise Chainlit should sign it
        "public_url": f"https://storage.googleapis.com/{bucket_name}/{object_name}",
    }


def land_route_map(
    origin: str,
    destination: str,
    mode: LandMode = "driving",
    out_html: str = "route_map.html",
    google_maps_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a land route using Google Directions API, save an interactive Folium HTML map,
    and upload the generated HTML to a GCS bucket.

    Args:
        origin (str): Origin address/place.
        destination (str): Destination address/place.
        mode (str, optional): "driving", "transit", "walking", "bicycling". Defaults to "driving".
        out_html (str, optional): Output HTML filename. Defaults to "route_map.html".
        google_maps_api_key (str, optional): Optional override. If not provided, reads GOOGLE_MAPS_API_KEY from config.py.

    Returns:
        dict: Result with keys:
            - status: "success" or "error"
            - distance_text, duration_text
            - duration_in_traffic_text (driving only if available)
            - out_html (local filename)
            - gcs_bucket, gcs_object, gcs_uri, public_url (upload results)
            - error_message (if error)
    """
    try:
        cfg_api_key, maps_bucket, maps_folder = _load_backend_config()
        api_key = google_maps_api_key or cfg_api_key

        if not api_key:
            return {
                "status": "error",
                "error_message": "Missing GOOGLE_MAPS_API_KEY in config.py (or pass google_maps_api_key).",
            }

        if not maps_bucket:
            return {
                "status": "error",
                "error_message": "Missing MAPS_GCS_BUCKET in config.py.",
            }

        if maps_folder is None:
            return {
                "status": "error",
                "error_message": "Missing MAPS_GCS_FOLDER in config.py.",
            }

        # 1) Fetch route
        gmaps = googlemaps.Client(key=api_key)
        now = datetime.now()

        kwargs = dict(
            origin=origin,
            destination=destination,
            mode=mode,
            alternatives=False,
        )

        # For driving/transit, departure_time improves results (traffic/transit schedules)
        if mode in ("driving", "transit"):
            kwargs["departure_time"] = now

        routes = gmaps.directions(**kwargs)
        if not routes:
            return {"status": "error", "error_message": "No route found. Try more specific origin/destination."}

        route = routes[0]
        leg = route["legs"][0]

        # 2) Build Folium map from overview polyline
        start_loc = leg["start_location"]
        end_loc = leg["end_location"]

        points = polyline.decode(route["overview_polyline"]["points"])

        mid_lat = (start_loc["lat"] + end_loc["lat"]) / 2
        mid_lng = (start_loc["lng"] + end_loc["lng"]) / 2

        m = folium.Map(location=[mid_lat, mid_lng], zoom_start=12, tiles="OpenStreetMap")
        folium.Marker([start_loc["lat"], start_loc["lng"]], popup=f"Start: {origin}").add_to(m)
        folium.Marker([end_loc["lat"], end_loc["lng"]], popup=f"End: {destination}").add_to(m)
        folium.PolyLine(points, color="blue", weight=6, opacity=0.85).add_to(m)
        m.fit_bounds(points)

        m.save(out_html)

        # 3) Upload to GCS (so Chainlit can fetch/sign and render)
        gcs_info = _upload_html_to_gcs(
            local_path=out_html,
            bucket_name=maps_bucket,
            folder=maps_folder,
            dest_filename=out_html,  # upload as same filename under folder
        )

        # 4) Structured response
        resp: Dict[str, Any] = {
            "status": "success",
            "mode": mode,
            "distance_text": leg["distance"]["text"],
            "duration_text": leg["duration"]["text"],
            "out_html": out_html,
            **gcs_info,
            "note": (
                "Map uploaded to GCS. If bucket is private, generate a signed URL in Chainlit "
                "and iframe that signed URL."
            ),
        }

        if mode == "driving":
            dit = leg.get("duration_in_traffic")
            if dit:
                resp["duration_in_traffic_text"] = dit.get("text")

        return resp

    except Exception as e:
        return {"status": "error", "error_message": str(e)}
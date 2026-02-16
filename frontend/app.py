from __future__ import annotations

import os
import re
import uuid
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import chainlit as cl
import requests
from google.cloud import storage


# ----------------------------
# Config
# ----------------------------
# ADK API server (local or deployed)
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
APP_NAME = os.environ.get("APP_NAME", "agent")

# Optional: if your backend is protected behind a gateway, pass a bearer token
BACKEND_BEARER_TOKEN = os.environ.get("BACKEND_BEARER_TOKEN", "").strip()

# GCS map config
GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))

# HTTP settings
HTTP_TIMEOUT_SEC = float(os.environ.get("HTTP_TIMEOUT_SEC", "60"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chainlit-frontend")


# ----------------------------
# Helpers: Intent
# ----------------------------
def wants_map(text: str) -> bool:
    """Minimal intent check; expand keywords as needed."""
    t = text.lower()
    return (
        "map" in t
        or "show map" in t
        or "generate map" in t
        or "route" in t
        or "tracks" in t
        or re.search(r"\broute\b.*\bfrom\b.*\bto\b", t) is not None
    )


# ----------------------------
# Helpers: GCS signed URL + map rendering
# ----------------------------
def generate_signed_map_url(bucket: str, object_name: str, ttl_min: int = 30) -> str:
    """
    Create a V4 signed GET URL for the map HTML stored in GCS.
    """
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_name)
    return blob.generate_signed_url(
        version="v4",
        method="GET",
        expiration=timedelta(minutes=ttl_min),
        response_disposition="inline",
    )


async def render_map(map_url: str, title: str = "Route Map"):
    """
    Render map in chat using a CustomElement.
    """
    map_el = cl.CustomElement(
        name="RouteMap",
        props={"src": map_url, "height": 420, "title": title},
        display="inline",
    )
    await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# ----------------------------
# Helpers: ADK backend connectivity
# ----------------------------
def _auth_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if BACKEND_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {BACKEND_BEARER_TOKEN}"
    return headers


def create_session_sync(app_name: str, user_id: str, session_id: str) -> Dict[str, Any]:
    """
    ADK API server: Create a new session.
    Endpoint shape: POST /apps/{app_name}/users/{user_id}/sessions/{session_id} [1](https://google.github.io/adk-docs/runtime/api-server/)
    """
    url = f"{BACKEND_URL}/apps/{app_name}/users/{user_id}/sessions/{session_id}"
    resp = requests.post(url, headers=_auth_headers(), json={}, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.json()


def run_agent_sync(app_name: str, user_id: str, session_id: str, text: str) -> List[Dict[str, Any]]:
    """
    ADK API server: Run agent (non-streaming).
    Endpoint: POST /run returns a JSON array of Event objects [1](https://google.github.io/adk-docs/runtime/api-server/)[2](https://google.github.io/adk-docs/api-reference/rest/)

    Uses camelCase fields as shown in docs: appName/userId/sessionId/newMessage [1](https://google.github.io/adk-docs/runtime/api-server/)[2](https://google.github.io/adk-docs/api-reference/rest/)
    """
    payload = {
        "appName": app_name,
        "userId": user_id,
        "sessionId": session_id,
        "newMessage": {
            "role": "user",
            "parts": [{"text": text}],
        },
        "streaming": False,
    }

    resp = requests.post(
        f"{BACKEND_URL}/run",
        headers=_auth_headers(),
        json=payload,
        timeout=HTTP_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


def extract_last_model_text(events: List[Dict[str, Any]]) -> str:
    """
    ADK /run returns an array of events, where model outputs appear in
    events with content.role == "model" and parts containing {text: "..."} [1](https://google.github.io/adk-docs/runtime/api-server/)[2](https://google.github.io/adk-docs/api-reference/rest/)
    """
    model_events = [e for e in events if e.get("content", {}).get("role") == "model"]
    if not model_events:
        return "⚠️ No model response found."

    last = model_events[-1]
    parts = last.get("content", {}).get("parts", [])
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            return p["text"]

    return "⚠️ Model responded, but no text part was found."


async def create_session(app_name: str, user_id: str, session_id: str) -> Dict[str, Any]:
    # run sync I/O in a worker thread to avoid blocking the event loop
    return await asyncio.to_thread(create_session_sync, app_name, user_id, session_id)


async def run_agent(app_name: str, user_id: str, session_id: str, text: str) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(run_agent_sync, app_name, user_id, session_id, text)


# ----------------------------
# Chainlit lifecycle
# ----------------------------
@cl.on_chat_start
async def start():
    # A stable user id if Chainlit auth is enabled; otherwise fallback.
    user = cl.user_session.get("user")
    user_id = user.identifier if user else "anonymous"

    # Unique session id per chat
    session_id = f"cl-{uuid.uuid4().hex}"

    cl.user_session.set("user_id", user_id)
    cl.user_session.set("session_id", session_id)

    # Welcome UI (your existing HTML)
    welcome_html = (
        '<div class="scmgpt-container">'
        '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
        '<div class="welcome-area">'
        '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
        '<p class="sub-headline">Ask me anything. Say “show map” to render the latest uploaded map from GCS.</p>'
        "</div>"
        "</div>"
    )
    await cl.Message(content=welcome_html).send()

    # Create backend session (required for ADK session-based continuity) [1](https://google.github.io/adk-docs/runtime/api-server/)
    try:
        await create_session(APP_NAME, user_id, session_id)
        logger.info("Backend session created app=%s user=%s session=%s", APP_NAME, user_id, session_id)
    except requests.HTTPError as e:
        # ADK mentions creating the exact same session again yields "Session already exists" [1](https://google.github.io/adk-docs/runtime/api-server/)
        await cl.Message(content=f"❌ Backend session create failed: {e}").send()
    except Exception as e:
        await cl.Message(content=f"❌ Backend session create failed: {e}").send()


@cl.on_message
async def main(message: cl.Message):
    user_text = (message.content or "").strip()
    user_id = cl.user_session.get("user_id")
    session_id = cl.user_session.get("session_id")

    if not user_id or not session_id:
        await cl.Message(content="⚠️ No active session. Please refresh the app.").send()
        return

    # 1) Call ADK backend for an actual agent response (/run) [1](https://google.github.io/adk-docs/runtime/api-server/)[2](https://google.github.io/adk-docs/api-reference/rest/)
    thinking = cl.Message(content="⏳ Running your request with the backend agent…")
    await thinking.send()

    try:
        events = await run_agent(APP_NAME, user_id, session_id, user_text)
        answer = extract_last_model_text(events)
        thinking.content = answer
        await thinking.update()
    except Exception as e:
        thinking.content = f"❌ Backend /run failed: {e}"
        await thinking.update()
        return

    # 2) Only render map when asked (your prior UI behavior)
    if wants_map(user_text):
        await cl.Message(content="🗺️ Fetching latest map from GCS…").send()
        try:
            url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
            await render_map(url, title="Latest Uploaded Map (GCS)")
        except Exception as e:
            await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()













# WITH MAP INCORPORATED

# from __future__ import annotations

# import os
# import re
# from datetime import timedelta

# import chainlit as cl
# from google.cloud import storage

# # --- Config ---
# GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
# GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
# SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))


# def wants_map(text: str) -> bool:
#     """
#     Minimal intent check. Expand keywords as needed.
#     """
#     t = text.lower()
#     return (
#         "map" in t
#         or "show map" in t
#         or "generate map" in t
#         or "route" in t
#         or "tracks" in t
#         or re.search(r"\broute\b.*\bfrom\b.*\bto\b", t) is not None
#     )


# def generate_signed_map_url(bucket: str, object_name: str, ttl_min: int = 30) -> str:
#     """
#     Create a V4 signed GET URL for the map HTML stored in GCS.
#     Signed URLs provide time-limited access to Cloud Storage objects. [1](https://docs.chainlit.io/guides/iframe)[2](https://docs.chainlit.io/api-reference/elements/custom)
#     """
#     client = storage.Client()
#     blob = client.bucket(bucket).blob(object_name)

#     return blob.generate_signed_url(
#         version="v4",
#         method="GET",
#         expiration=timedelta(minutes=ttl_min),
#         response_disposition="inline",
#     )


# async def render_map(map_url: str, title: str = "Route Map"):
#     """
#     Render map in chat using a CustomElement (rendered as an element attached to a message). [3](https://deepwiki.com/Chainlit/chainlit/12-configuration-reference)[4](https://deepwiki.com/Chainlit/chainlit/12.3-ui-configuration)
#     """
#     map_el = cl.CustomElement(
#         name="RouteMap",
#         props={"src": map_url, "height": 420, "title": title},
#         display="inline",
#     )
#     await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# @cl.on_chat_start
# async def start():
#     welcome_html = (
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">Ask me “show map” to render the latest uploaded map from GCS.</p>'
#         '</div>'
#         '</div>'
#     )
#     await cl.Message(content=welcome_html).send()


# @cl.on_message
# async def main(message: cl.Message):
#     user_text = message.content.strip()

#     # Only show map when asked
#     if not wants_map(user_text):
#         await cl.Message(content=f"Analyzing logistics data for: {user_text}...").send()
#         return

#     await cl.Message(content="🗺️ Fetching latest map from GCS…").send()

#     try:
#         url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
#         await render_map(url, title="Latest Uploaded Map (GCS)")
#     except Exception as e:
#         await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()











# import os
# import shutil
# import chainlit as cl

# PUBLIC_MAP_PATH = "public/maps/route_map.html"
# IFRAME_SRC = "/public/maps/route_map.html"


# def publish_latest_map(local_map_path: str = "route_map.html") -> bool:
#     """Copy generated map HTML into Chainlit public folder so iframe can load it."""
#     os.makedirs(os.path.dirname(PUBLIC_MAP_PATH), exist_ok=True)

#     if not os.path.exists(local_map_path):
#         return False

#     shutil.copyfile(local_map_path, PUBLIC_MAP_PATH)
#     return True


# @cl.on_chat_start
# async def start():
#     # Publish if exists
#     publish_latest_map("route_map.html")

#     # Render the map element at the top
#     map_el = cl.CustomElement(
#         name="RouteMap",
#         props={"src": IFRAME_SRC, "height": 420, "title": "Route Map"},
#         display="inline",
#     )
#     # Elements are attached to messages in Chainlit UI. [3](https://discuss.ai.google.dev/t/how-to-use-google-search-with-gemini-2-0-flash-and-google-vertexai-on-langchain-chatbot/71823)
#     await cl.Message(content="", elements=[map_el]).send()

#     welcome_html = (
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">How can I optimize your flow today?</p>'
#         '<div class="action-grid">'
#         '<div class="action-card">'
#         '<span class="card-icon">🚢</span>'
#         '<span class="card-text">Predict potential delays in the Suez Canal for Q3...</span>'
#         '</div>'
#         '<div class="action-card">'
#         '<span class="card-icon">✈️</span>'
#         '<span class="card-text">Compare air vs. ocean freight costs for SKU-882...</span>'
#         '</div>'
#         '</div>'
#         '</div>'
#         '</div>'
#     )
#     await cl.Message(content=welcome_html).send()


# @cl.on_message
# async def main(message: cl.Message):
#     await cl.Message(content=f"Analyzing logistics data for: {message.content}...").send()

#     # After your ADK tool regenerates route_map.html, republish it
#     if publish_latest_map("route_map.html"):
#         map_el = cl.CustomElement(
#             name="RouteMap",
#             props={"src": IFRAME_SRC, "height": 420, "title": "Updated Route Map"},
#             display="inline",
#         )
#         await cl.Message(content="✅ Map updated:", elements=[map_el]).send()
#     else:
#         await cl.Message(content="⚠️ route_map.html not found. Generate it first.").send()






























# BACKUP
# import chainlit as cl

# @cl.on_chat_start
# async def start():
#     # We construct the HTML as a single line to guarantee no "Code Block" triggering
#     welcome_html = (
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">How can I optimize your flow today?</p>'
#         '<div class="action-grid">'
#         '<div class="action-card">'
#         '<span class="card-icon">🚢</span>'
#         '<span class="card-text">Predict potential delays in the Suez Canal for Q3...</span>'
#         '</div>'
#         '<div class="action-card">'
#         '<span class="card-icon">✈️</span>'
#         '<span class="card-text">Compare air vs. ocean freight costs for SKU-882...</span>'
#         '</div>'
#         '</div>'
#         '</div>'
#         '</div>'
#     )
    
#     await cl.Message(content=welcome_html).send()

# @cl.on_message
# async def main(message: cl.Message):
#     await cl.Message(content=f"Analyzing logistics data for: {message.content}...").send()
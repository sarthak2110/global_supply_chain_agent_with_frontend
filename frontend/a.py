from __future__ import annotations

import os
import re
import json
import httpx
import logging
from datetime import timedelta

import chainlit as cl
from google.cloud import storage
import google.auth
import google.auth.transport.requests

# ----------------------------
# Config
# ----------------------------
PROJECT_ID = "saas-poc-env"
LOCATION = "us-central1"
ENGINE_ID = "1801053372611035136"

AGENT_ENGINE_QUERY_URL = os.environ.get(
    "AGENT_ENGINE_QUERY_URL",
    f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:query"
)
AGENT_ENGINE_STREAM_URL = os.environ.get(
    "AGENT_ENGINE_STREAM_URL",
    f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"
)

GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chainlit-frontend")


# ----------------------------
# Helpers: Intent & Maps
# ----------------------------

def wants_map(text: str) -> bool:
    t = text.lower()
    return (
        "map" in t or "show map" in t or "generate map" in t
        or "route" in t or "tracks" in t
        or re.search(r"\broute\b.*\bfrom\b.*\bto\b", t) is not None
    )

def generate_signed_map_url(bucket: str, object_name: str, ttl_min: int = 30) -> str:
    credentials, project = google.auth.default()
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)

    client = storage.Client(project=project, credentials=credentials)
    blob = client.bucket(bucket).blob(object_name)
    
    kwargs = {
        "version": "v4",
        "method": "GET",
        "expiration": timedelta(minutes=ttl_min),
        "response_disposition": "inline",
    }

    if hasattr(credentials, "service_account_email") and credentials.service_account_email:
        kwargs["service_account_email"] = credentials.service_account_email
        kwargs["access_token"] = credentials.token

    return blob.generate_signed_url(**kwargs)

async def render_map(map_url: str, title: str = "Route Map"):
    map_el = cl.CustomElement(
        name="RouteMap",
        props={"src": map_url, "height": 420, "title": title},
        display="inline",
    )
    await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# ----------------------------
# Helper: Authentication
# ----------------------------
def get_bearer_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    return creds.token


# ----------------------------
# Chainlit Lifecycle
# ----------------------------
@cl.on_chat_start
async def start():
    if cl.user_session.get("initialized"):
        return
    cl.user_session.set("initialized", True)

    user = cl.user_session.get("user")
    user_id = user.identifier if user else "logistics-manager"
    cl.user_session.set("user_id", user_id)

    welcome_html = (
        '<style>.MuiAvatar-root { display: none !important; }</style>'
        '<div class="scmgpt-container">'
        '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
        '<div class="welcome-area">'
        '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
        '<p class="sub-headline">Where should we start?</p>'
        "</div>"
        "</div>"
    )
    await cl.Message(content=welcome_html).send()
    
    payload = {
        "class_method": "async_create_session",
        "input": {"user_id": user_id}
    }
    
    headers = {
        "Authorization": f"Bearer {get_bearer_token()}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(AGENT_ENGINE_QUERY_URL, json=payload, headers=headers)
            
            if resp.status_code != 200:
                await cl.Message(content=f"❌ **Backend session creation failed ({resp.status_code}):**\n{resp.text}").send()
                return
                
            data = resp.json()
            session_data = data.get("output", {})
            backend_session_id = session_data.get("id") or session_data.get("session_id")
            
            if not backend_session_id:
                raise ValueError(f"Could not find a valid session ID in the backend response: {data}")

            cl.user_session.set("session_id", backend_session_id)
            logger.info("Successfully created backend session: user=%s session=%s", user_id, backend_session_id)
            
    except Exception as e:
        logger.error(f"Session initialization error: {e}")
        await cl.Message(content=f"❌ **Critical Error initializing backend session:** {str(e)}").send()


@cl.on_message
async def main(message: cl.Message):
    user_text = message.content.strip()
    user_id = cl.user_session.get("user_id")
    session_id = cl.user_session.get("session_id")
    
    if not user_id or not session_id:
        await cl.Message(content="⚠️ No active session. Please refresh the app to reconnect.").send()
        return

    # Default to Central Orchestrator if no transfer happens
    current_agent = "Central Orchestrator"
    
    # Do NOT send the message immediately. This keeps the 3 dots bouncing!
    msg = cl.Message(content="")
    message_started = False

    payload = {
        "class_method": "async_stream_query",
        "input": {
            "user_id": user_id,
            "session_id": session_id,
            "message": user_text 
        }
    }

    headers = {
        "Authorization": f"Bearer {get_bearer_token()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", AGENT_ENGINE_STREAM_URL, json=payload, headers=headers) as response:
                
                if response.status_code != 200:
                    error_data = await response.aread()
                    msg.content = f"❌ **API Error {response.status_code}:**\n{error_data.decode()}"
                    await msg.send()
                    return

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                        
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                    else:
                        data_str = line
                        
                    if data_str == "[DONE]":
                        continue
                        
                    try:
                        chunk = json.loads(data_str)
                        content = chunk.get("content")
                        
                        if isinstance(content, str):
                            if not message_started:
                                # Inject the agent name on the very first text chunk
                                msg.content = f"**Agent:** `{current_agent}`\n\n"
                                await msg.send()
                                message_started = True
                            await msg.stream_token(content)
                            
                        elif isinstance(content, dict):
                            parts = content.get("parts", [])
                            for part in parts:
                                
                                # -------------------------------------------------------------
                                # SILENTLY SNIFF THE BACKEND ARGS TO FIND THE SUB-AGENT
                                # -------------------------------------------------------------
                                if "function_call" in part:
                                    args = part["function_call"].get("args", {})
                                    
                                    # Hunt for the specific agent name in the arguments
                                    for key, val in args.items():
                                        if isinstance(val, str) and "agent" in val.lower():
                                            current_agent = val.replace("_", " ").title()
                                            break
                                        elif key in ["agent", "agent_name", "target", "name"]:
                                            current_agent = str(val).replace("_", " ").title()
                                            break
                                            
                                    # We DO NOT print anything here. The typing dots keep bouncing!

                                # -------------------------------------------------------------
                                # PRINT THE TEXT WITH THE DETECTED AGENT
                                # -------------------------------------------------------------
                                if "text" in part:
                                    if not message_started:
                                        # Inject the agent name on the very first text chunk
                                        msg.content = f"**Agent:** `{current_agent}`\n\n"
                                        await msg.send()
                                        message_started = True
                                    await msg.stream_token(part["text"])
                                    
                    except json.JSONDecodeError:
                        continue

    except Exception as e:
        if not message_started:
            msg.content = f"❌ **Client Error:** {str(e)}"
            await msg.send()
        else:
            msg.content += f"\n\n⚠️ **Client Error:** {str(e)}"
        logger.error(f"EXCEPTION: {str(e)}")

    finally:
        msg.content = msg.content.replace('Root Agent','Central Orchestrator')
        if not message_started:
            msg.content = "✅ *Action completed.*"
            await msg.send()
        else:
            await msg.update()

    if wants_map(user_text):
        await cl.Message(content="🗺️ Fetching latest map from GCS…").send()
        try:
            url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
            await render_map(url, title="Latest Uploaded Map (GCS)")
        except Exception as e:
            await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()
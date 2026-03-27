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

def detect_active_agent(text: str) -> str:
    """Smart Intent Fallback: Predicts the ADK sub-agent based on the prompt."""
    t = text.lower()
    if any(word in t for word in ["inventory", "stock", "warehouse", "sku", "product", "selling", "sales", "shortage", "low"]):
        return "Inventory Analyst"
    elif any(word in t for word in ["route", "map", "logistic", "transit", "shipment", "delay", "track", "location"]):
        return "Logistics Resolver"
    elif any(word in t for word in ["supplier", "negotiat", "quote", "vendor", "price", "cost", "purchase"]):
        return "Supplier Negotiator"
    return "Central Orchestrator"

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

    # 1. We determine the sub-agent and create an empty message
    current_agent = detect_active_agent(user_text)
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
                                # 2. INJECT THE AGENT NAME INTO THE CHAT BUBBLE!
                                msg.content = f"**Agent:** `{current_agent}`\n\n"
                                await msg.send()
                                message_started = True
                            await msg.stream_token(content)
                            
                        elif isinstance(content, dict):
                            parts = content.get("parts", [])
                            for part in parts:
                                # We silently ignore ADK 'function_call' parts so no ugly boxes appear!
                                if "text" in part:
                                    if not message_started:
                                        # 2. INJECT THE AGENT NAME INTO THE CHAT BUBBLE!
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


# from __future__ import annotations

# import os
# import re
# import json
# import httpx
# import logging
# from datetime import timedelta

# import chainlit as cl
# from google.cloud import storage
# import google.auth
# import google.auth.transport.requests

# # ----------------------------
# # Config
# # ----------------------------
# PROJECT_ID = "saas-poc-env"
# LOCATION = "us-central1"
# ENGINE_ID = "1801053372611035136"

# # Endpoints for two-step connection
# AGENT_ENGINE_QUERY_URL = os.environ.get(
#     "AGENT_ENGINE_QUERY_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:query"
# )
# AGENT_ENGINE_STREAM_URL = os.environ.get(
#     "AGENT_ENGINE_STREAM_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"
# )

# # GCS map config
# GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
# GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
# SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("chainlit-frontend")


# # ----------------------------
# # Helpers: Intent & Maps
# # ----------------------------
# def wants_map(text: str) -> bool:
#     """Minimal intent check to see if the user asked for a map."""
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
#     """Create a V4 signed GET URL for the map HTML stored in GCS, compatible with Cloud Run."""
#     credentials, project = google.auth.default()
#     auth_req = google.auth.transport.requests.Request()
#     credentials.refresh(auth_req)

#     client = storage.Client(project=project, credentials=credentials)
#     blob = client.bucket(bucket).blob(object_name)
    
#     kwargs = {
#         "version": "v4",
#         "method": "GET",
#         "expiration": timedelta(minutes=ttl_min),
#         "response_disposition": "inline",
#     }

#     if hasattr(credentials, "service_account_email") and credentials.service_account_email:
#         kwargs["service_account_email"] = credentials.service_account_email
#         kwargs["access_token"] = credentials.token

#     return blob.generate_signed_url(**kwargs)

# async def render_map(map_url: str, title: str = "Route Map"):
#     """Render map in chat using a CustomElement."""
#     map_el = cl.CustomElement(
#         name="RouteMap",
#         props={"src": map_url, "height": 420, "title": title},
#         display="inline",
#     )
#     await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# # ----------------------------
# # Helper: Authentication
# # ----------------------------
# def get_bearer_token() -> str:
#     """Fetch an OAuth 2.0 token using default GCP credentials."""
#     creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
#     auth_req = google.auth.transport.requests.Request()
#     creds.refresh(auth_req)
#     return creds.token


# # ----------------------------
# # Chainlit Lifecycle
# # ----------------------------
# @cl.on_chat_start
# async def start():
#     if cl.user_session.get("initialized"):
#         return
#     cl.user_session.set("initialized", True)

#     user = cl.user_session.get("user")
#     user_id = user.identifier if user else "logistics-manager"
#     cl.user_session.set("user_id", user_id)

#     welcome_html = (
#         '<style>.MuiAvatar-root { display: none !important; }</style>'
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">Ask me anything. Say “show map” to render the latest uploaded map from GCS.</p>'
#         "</div>"
#         "</div>"
#     )
#     await cl.Message(content=welcome_html).send()
    
#     payload = {
#         "class_method": "async_create_session",
#         "input": {"user_id": user_id}
#     }
    
#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             resp = await client.post(AGENT_ENGINE_QUERY_URL, json=payload, headers=headers)
            
#             if resp.status_code != 200:
#                 await cl.Message(content=f"❌ **Backend session creation failed ({resp.status_code}):**\n{resp.text}").send()
#                 return
                
#             data = resp.json()
#             session_data = data.get("output", {})
#             backend_session_id = session_data.get("id") or session_data.get("session_id")
            
#             if not backend_session_id:
#                 raise ValueError(f"Could not find a valid session ID in the backend response: {data}")

#             cl.user_session.set("session_id", backend_session_id)
#             logger.info("Successfully created backend session: user=%s session=%s", user_id, backend_session_id)
            
#     except Exception as e:
#         logger.error(f"Session initialization error: {e}")
#         await cl.Message(content=f"❌ **Critical Error initializing backend session:** {str(e)}").send()


# @cl.on_message
# async def main(message: cl.Message):
#     user_text = message.content.strip()
#     user_id = cl.user_session.get("user_id")
#     session_id = cl.user_session.get("session_id")
    
#     if not user_id or not session_id:
#         await cl.Message(content="⚠️ No active session. Please refresh the app to reconnect.").send()
#         return

#     # Send the empty message IMMEDIATELY to trigger Chainlit's 3-dot typing animation
#     msg = cl.Message(content="")
#     await msg.send()

#     payload = {
#         "class_method": "async_stream_query",
#         "input": {
#             "user_id": user_id,
#             "session_id": session_id,
#             "message": user_text 
#         }
#     }

#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json",
#         "Accept": "text/event-stream"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=120.0) as client:
#             async with client.stream("POST", AGENT_ENGINE_STREAM_URL, json=payload, headers=headers) as response:
                
#                 if response.status_code != 200:
#                     error_data = await response.aread()
#                     msg.content = f"❌ **API Error {response.status_code}:**\n{error_data.decode()}"
#                     await msg.update()
#                     return

#                 async for line in response.aiter_lines():
#                     line = line.strip()
#                     if not line:
#                         continue
                        
#                     if line.startswith("data:"):
#                         data_str = line[5:].strip()
#                     else:
#                         data_str = line
                        
#                     if data_str == "[DONE]":
#                         continue
                        
#                     try:
#                         chunk = json.loads(data_str)
#                         content = chunk.get("content")
                        
#                         if isinstance(content, str):
#                             await msg.stream_token(content)
                            
#                         elif isinstance(content, dict):
#                             parts = content.get("parts", [])
#                             for part in parts:
#                                 if "text" in part:
#                                     await msg.stream_token(part["text"])
#                                 # We silently ignore "function_call" 
#                                 # The UI will continue bouncing the 3 dots while tools run!
                                    
#                     except json.JSONDecodeError:
#                         continue

#     except Exception as e:
#         msg.content += f"\n\n⚠️ **Client Error:** {str(e)}"
#         logger.error(f"EXCEPTION: {str(e)}")

#     finally:
#         # If the background tools finish and the model never streamed text, show a success message
#         if not msg.content:
#             msg.content = "✅ *Action completed.*"
#         await msg.update()

#     if wants_map(user_text):
#         await cl.Message(content="🗺️ Fetching latest map from GCS…").send()
#         try:
#             url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
#             await render_map(url, title="Latest Uploaded Map (GCS)")
#         except Exception as e:
#             await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()

# from __future__ import annotations

# import os
# import re
# import json
# import httpx
# import logging
# from datetime import timedelta

# import chainlit as cl
# from google.cloud import storage
# import google.auth
# import google.auth.transport.requests

# # ----------------------------
# # Config
# # ----------------------------
# PROJECT_ID = "saas-poc-env"
# LOCATION = "us-central1"
# ENGINE_ID = "1801053372611035136"

# # Endpoints for two-step connection
# AGENT_ENGINE_QUERY_URL = os.environ.get(
#     "AGENT_ENGINE_QUERY_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:query"
# )
# AGENT_ENGINE_STREAM_URL = os.environ.get(
#     "AGENT_ENGINE_STREAM_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"
# )

# # GCS map config
# GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
# GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
# SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("chainlit-frontend")


# # ----------------------------
# # Helpers: Intent & Maps
# # ----------------------------
# def wants_map(text: str) -> bool:
#     """Minimal intent check to see if the user asked for a map."""
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
#     """Create a V4 signed GET URL for the map HTML stored in GCS, compatible with Cloud Run."""
#     credentials, project = google.auth.default()
#     auth_req = google.auth.transport.requests.Request()
#     credentials.refresh(auth_req)

#     client = storage.Client(project=project, credentials=credentials)
#     blob = client.bucket(bucket).blob(object_name)
    
#     kwargs = {
#         "version": "v4",
#         "method": "GET",
#         "expiration": timedelta(minutes=ttl_min),
#         "response_disposition": "inline",
#     }

#     # If running on Cloud Run, force the use of remote IAM API for signing
#     if hasattr(credentials, "service_account_email") and credentials.service_account_email:
#         kwargs["service_account_email"] = credentials.service_account_email
#         kwargs["access_token"] = credentials.token

#     return blob.generate_signed_url(**kwargs)

# async def render_map(map_url: str, title: str = "Route Map"):
#     """Render map in chat using a CustomElement."""
#     map_el = cl.CustomElement(
#         name="RouteMap",
#         props={"src": map_url, "height": 420, "title": title},
#         display="inline",
#     )
#     await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# # ----------------------------
# # Helper: Authentication
# # ----------------------------
# def get_bearer_token() -> str:
#     """Fetch an OAuth 2.0 token using default GCP credentials."""
#     creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
#     auth_req = google.auth.transport.requests.Request()
#     creds.refresh(auth_req)
#     return creds.token


# # ----------------------------
# # Chainlit Lifecycle
# # ----------------------------
# @cl.on_chat_start
# async def start():
#     # Identify user
#     user = cl.user_session.get("user")
#     user_id = user.identifier if user else "logistics-manager"
#     cl.user_session.set("user_id", user_id)

#     # Added CSS to hide the Chainlit avatar/logo globally
#     welcome_html = (
#         '<style>.MuiAvatar-root { display: none !important; }</style>'
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">Ask me anything. Say “show map” to render the latest uploaded map from GCS.</p>'
#         "</div>"
#         "</div>"
#     )
#     await cl.Message(content=welcome_html).send()
    
#     # ---------------------------------------------------------
#     # 1) Secure Session Handshake
#     # ---------------------------------------------------------
#     init_msg = cl.Message(content="⏳ *Initializing secure connection to Agent Engine...*")
#     await init_msg.send()
    
#     payload = {
#         "class_method": "async_create_session",
#         "input": {"user_id": user_id}
#     }
    
#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             resp = await client.post(AGENT_ENGINE_QUERY_URL, json=payload, headers=headers)
            
#             if resp.status_code != 200:
#                 init_msg.content = f"❌ **Backend session creation failed ({resp.status_code}):**\n{resp.text}"
#                 await init_msg.update()
#                 return
                
#             data = resp.json()
#             session_data = data.get("output", {})
#             backend_session_id = session_data.get("id") or session_data.get("session_id")
            
#             if not backend_session_id:
#                 raise ValueError(f"Could not find a valid session ID in the backend response: {data}")

#             # Store the OFFICIAL backend session ID
#             cl.user_session.set("session_id", backend_session_id)
#             logger.info("Successfully created backend session: user=%s session=%s", user_id, backend_session_id)
            
#             init_msg.content = "✅ **Connection verified.** Ready for your queries."
#             await init_msg.update()
            
#     except Exception as e:
#         logger.error(f"Session initialization error: {e}")
#         init_msg.content = f"❌ **Critical Error initializing backend session:** {str(e)}"
#         await init_msg.update()


# @cl.on_message
# async def main(message: cl.Message):
#     user_text = message.content.strip()
#     user_id = cl.user_session.get("user_id")
#     session_id = cl.user_session.get("session_id")
    
#     if not user_id or not session_id:
#         await cl.Message(content="⚠️ No active session. Please refresh the app to reconnect.").send()
#         return

#     # 1. Create the message, but DO NOT send it yet. 
#     # This leaves Chainlit's default 3-dot typing animation running on the UI!
#     msg = cl.Message(content="")
#     message_started = False

#     # ---------------------------------------------------------
#     # 2) Stream using the official Session ID
#     # ---------------------------------------------------------
#     payload = {
#         "class_method": "async_stream_query",
#         "input": {
#             "user_id": user_id,
#             "session_id": session_id,
#             "message": user_text 
#         }
#     }

#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json",
#         "Accept": "text/event-stream"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=120.0) as client:
#             async with client.stream("POST", AGENT_ENGINE_STREAM_URL, json=payload, headers=headers) as response:
                
#                 if response.status_code != 200:
#                     error_data = await response.aread()
#                     msg.content = f"❌ **API Error {response.status_code}:**\n{error_data.decode()}"
#                     await msg.send()
#                     return

#                 async for line in response.aiter_lines():
#                     line = line.strip()
#                     if not line:
#                         continue
                        
#                     if line.startswith("data:"):
#                         data_str = line[5:].strip()
#                     else:
#                         data_str = line
                        
#                     if data_str == "[DONE]":
#                         continue
                        
#                     try:
#                         chunk = json.loads(data_str)
#                         content = chunk.get("content")
                        
#                         # 2. Only send the message when actual text arrives
#                         if isinstance(content, str):
#                             if not message_started:
#                                 await msg.send()
#                                 message_started = True
#                             await msg.stream_token(content)
                            
#                         elif isinstance(content, dict):
#                             parts = content.get("parts", [])
#                             for part in parts:
#                                 if "text" in part:
#                                     if not message_started:
#                                         await msg.send()
#                                         message_started = True
#                                     await msg.stream_token(part["text"])
                                    
#                                 # function_call parts are silently ignored.
#                                 # This keeps the dots bouncing while the agent works in the background!
                                    
#                     except json.JSONDecodeError:
#                         logger.warning(f"⚠️ JSON Decode Error on chunk: {data_str}")
#                         continue

#     except Exception as e:
#         if not message_started:
#             msg.content = f"❌ **Client Error:** {str(e)}"
#             await msg.send()
#         else:
#             msg.content += f"\n\n⚠️ **Client Error:** {str(e)}"
#         logger.error(f"EXCEPTION: {str(e)}")

#     finally:
#         # 3. Ensure message is finalized properly
#         if not message_started:
#             if not msg.content:
#                 msg.content = "✅ *Action completed in the background.*"
#             await msg.send()
#         else:
#             await msg.update()

#     # ---------------------------------------------------------
#     # 3) Render Maps if Requested
#     # ---------------------------------------------------------
#     if wants_map(user_text):
#         await cl.Message(content="🗺️ Fetching latest map from GCS…").send()
#         try:
#             url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
#             await render_map(url, title="Latest Uploaded Map (GCS)")
#         except Exception as e:
#             await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()





# from __future__ import annotations

# import os
# import re
# import json
# import httpx
# import logging
# from datetime import timedelta

# import chainlit as cl
# from google.cloud import storage
# import google.auth
# import google.auth.transport.requests

# # ----------------------------
# # Config
# # ----------------------------
# PROJECT_ID = "saas-poc-env"
# LOCATION = "us-central1"
# ENGINE_ID = "1801053372611035136"

# # Endpoints for two-step connection
# AGENT_ENGINE_QUERY_URL = os.environ.get(
#     "AGENT_ENGINE_QUERY_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:query"
# )
# AGENT_ENGINE_STREAM_URL = os.environ.get(
#     "AGENT_ENGINE_STREAM_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"
# )

# # GCS map config
# GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
# GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
# SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("chainlit-frontend")


# # ----------------------------
# # Helpers: Intent & Maps
# # ----------------------------
# def wants_map(text: str) -> bool:
#     """Minimal intent check to see if the user asked for a map."""
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
#     """Create a V4 signed GET URL for the map HTML stored in GCS, compatible with Cloud Run."""
#     credentials, project = google.auth.default()
#     auth_req = google.auth.transport.requests.Request()
#     credentials.refresh(auth_req)

#     client = storage.Client(project=project, credentials=credentials)
#     blob = client.bucket(bucket).blob(object_name)
    
#     kwargs = {
#         "version": "v4",
#         "method": "GET",
#         "expiration": timedelta(minutes=ttl_min),
#         "response_disposition": "inline",
#     }

#     # If running on Cloud Run, force the use of remote IAM API for signing
#     if hasattr(credentials, "service_account_email") and credentials.service_account_email:
#         kwargs["service_account_email"] = credentials.service_account_email
#         kwargs["access_token"] = credentials.token

#     return blob.generate_signed_url(**kwargs)

# async def render_map(map_url: str, title: str = "Route Map"):
#     """Render map in chat using a CustomElement."""
#     map_el = cl.CustomElement(
#         name="RouteMap",
#         props={"src": map_url, "height": 420, "title": title},
#         display="inline",
#     )
#     await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# # ----------------------------
# # Helper: Authentication
# # ----------------------------
# def get_bearer_token() -> str:
#     """Fetch an OAuth 2.0 token using default GCP credentials."""
#     creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
#     auth_req = google.auth.transport.requests.Request()
#     creds.refresh(auth_req)
#     return creds.token


# # ----------------------------
# # Chainlit Lifecycle
# # ----------------------------
# @cl.on_chat_start
# async def start():
#     # Identify user
#     user = cl.user_session.get("user")
#     user_id = user.identifier if user else "logistics-manager"
#     cl.user_session.set("user_id", user_id)

#     # Added CSS to hide the Chainlit avatar/logo globally
#     welcome_html = (
#         '<style>.MuiAvatar-root { display: none !important; }</style>'
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">Ask me anything. Say “show map” to render the latest uploaded map from GCS.</p>'
#         "</div>"
#         "</div>"
#     )
#     await cl.Message(content=welcome_html).send()
    
#     # ---------------------------------------------------------
#     # 1) Secure Session Handshake
#     # ---------------------------------------------------------
#     init_msg = cl.Message(content="⏳ *Initializing secure connection to Agent Engine...*")
#     await init_msg.send()
    
#     payload = {
#         "class_method": "async_create_session",
#         "input": {"user_id": user_id}
#     }
    
#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             resp = await client.post(AGENT_ENGINE_QUERY_URL, json=payload, headers=headers)
            
#             if resp.status_code != 200:
#                 init_msg.content = f"❌ **Backend session creation failed ({resp.status_code}):**\n{resp.text}"
#                 await init_msg.update()
#                 return
                
#             data = resp.json()
#             session_data = data.get("output", {})
#             backend_session_id = session_data.get("id") or session_data.get("session_id")
            
#             if not backend_session_id:
#                 raise ValueError(f"Could not find a valid session ID in the backend response: {data}")

#             # Store the OFFICIAL backend session ID
#             cl.user_session.set("session_id", backend_session_id)
#             logger.info("Successfully created backend session: user=%s session=%s", user_id, backend_session_id)
            
#             init_msg.content = "✅ **Connection verified.** Ready for your queries."
#             await init_msg.update()
            
#     except Exception as e:
#         logger.error(f"Session initialization error: {e}")
#         init_msg.content = f"❌ **Critical Error initializing backend session:** {str(e)}"
#         await init_msg.update()


# @cl.on_message
# async def main(message: cl.Message):
#     user_text = message.content.strip()
#     user_id = cl.user_session.get("user_id")
#     session_id = cl.user_session.get("session_id")
    
#     if not user_id or not session_id:
#         await cl.Message(content="⚠️ No active session. Please refresh the app to reconnect.").send()
#         return

#     msg = cl.Message(content="")
#     await msg.send()

#     # ---------------------------------------------------------
#     # 2) Stream using the official Session ID
#     # ---------------------------------------------------------
#     payload = {
#         "class_method": "async_stream_query",
#         "input": {
#             "user_id": user_id,
#             "session_id": session_id,
#             "message": user_text 
#         }
#     }

#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json",
#         "Accept": "text/event-stream"
#     }

#     active_tool_step = None

#     try:
#         async with httpx.AsyncClient(timeout=120.0) as client:
#             async with client.stream("POST", AGENT_ENGINE_STREAM_URL, json=payload, headers=headers) as response:
                
#                 if response.status_code != 200:
#                     error_data = await response.aread()
#                     msg.content = f"❌ **API Error {response.status_code}:**\n{error_data.decode()}"
#                     await msg.update()
#                     return

#                 async for line in response.aiter_lines():
#                     line = line.strip()
#                     if not line:
#                         continue
                        
#                     if line.startswith("data:"):
#                         data_str = line[5:].strip()
#                     else:
#                         data_str = line
                        
#                     if data_str == "[DONE]":
#                         continue
                        
#                     try:
#                         chunk = json.loads(data_str)
#                         content = chunk.get("content")
                        
#                         if isinstance(content, str):
#                             if active_tool_step:
#                                 active_tool_step.status = "success"
#                                 await active_tool_step.update()
#                                 active_tool_step = None
                                
#                             await msg.stream_token(content)
                            
#                         elif isinstance(content, dict):
#                             parts = content.get("parts", [])
#                             for part in parts:
#                                 if "text" in part:
#                                     if active_tool_step:
#                                         active_tool_step.status = "success"
#                                         await active_tool_step.update()
#                                         active_tool_step = None
                                        
#                                     await msg.stream_token(part["text"])
                                    
#                                 elif "function_call" in part:
#                                     func_name = part["function_call"].get("name", "tool")
                                    
#                                     if active_tool_step:
#                                         active_tool_step.status = "success"
#                                         await active_tool_step.update()
                                        
#                                     active_tool_step = cl.Step(name=f"Running {func_name}...", type="tool")
#                                     await active_tool_step.send()
                                    
#                     except json.JSONDecodeError:
#                         logger.warning(f"⚠️ JSON Decode Error on chunk: {data_str}")
#                         continue

#     except Exception as e:
#         msg.content += f"\n\n⚠️ **Client Error:** {str(e)}"
#         logger.error(f"EXCEPTION: {str(e)}")

#     finally:
#         if active_tool_step:
#             active_tool_step.status = "success"
#             await active_tool_step.update()
            
#         await msg.update()

#     # ---------------------------------------------------------
#     # 3) Render Maps if Requested
#     # ---------------------------------------------------------
#     if wants_map(user_text):
#         await cl.Message(content="🗺️ Fetching latest map from GCS…").send()
#         try:
#             url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
#             await render_map(url, title="Latest Uploaded Map (GCS)")
#         except Exception as e:
#             await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()






# from __future__ import annotations

# import os
# import re
# import json
# import httpx
# import logging
# from datetime import timedelta

# import chainlit as cl
# from google.cloud import storage
# import google.auth
# import google.auth.transport.requests

# # ----------------------------
# # Config
# # ----------------------------
# PROJECT_ID = "saas-poc-env"
# LOCATION = "us-central1"
# ENGINE_ID = "1801053372611035136"

# # Endpoints for two-step connection
# AGENT_ENGINE_QUERY_URL = os.environ.get(
#     "AGENT_ENGINE_QUERY_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:query"
# )
# AGENT_ENGINE_STREAM_URL = os.environ.get(
#     "AGENT_ENGINE_STREAM_URL",
#     f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"
# )

# # GCS map config
# GCS_BUCKET = os.environ.get("GCS_BUCKET", "sarthak-test")
# GCS_OBJECT = os.environ.get("GCS_OBJECT", "maps/route_map.html")
# SIGNED_URL_TTL_MIN = int(os.environ.get("SIGNED_URL_TTL_MIN", "30"))

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("chainlit-frontend")


# # ----------------------------
# # Helpers: Intent & Maps
# # ----------------------------
# def wants_map(text: str) -> bool:
#     """Minimal intent check to see if the user asked for a map."""
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
#     """Create a V4 signed GET URL for the map HTML stored in GCS, compatible with Cloud Run."""
#     credentials, project = google.auth.default()
#     auth_req = google.auth.transport.requests.Request()
#     credentials.refresh(auth_req)

#     client = storage.Client(project=project, credentials=credentials)
#     blob = client.bucket(bucket).blob(object_name)
    
#     kwargs = {
#         "version": "v4",
#         "method": "GET",
#         "expiration": timedelta(minutes=ttl_min),
#         "response_disposition": "inline",
#     }

#     # If running on Cloud Run, force the use of remote IAM API for signing
#     if hasattr(credentials, "service_account_email") and credentials.service_account_email:
#         kwargs["service_account_email"] = credentials.service_account_email
#         kwargs["access_token"] = credentials.token

#     return blob.generate_signed_url(**kwargs)

# async def render_map(map_url: str, title: str = "Route Map"):
#     """Render map in chat using a CustomElement."""
#     map_el = cl.CustomElement(
#         name="RouteMap",
#         props={"src": map_url, "height": 420, "title": title},
#         display="inline",
#     )
#     await cl.Message(content="✅ Map ready:", elements=[map_el]).send()


# # ----------------------------
# # Helper: Authentication
# # ----------------------------
# def get_bearer_token() -> str:
#     """Fetch an OAuth 2.0 token using default GCP credentials."""
#     creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
#     auth_req = google.auth.transport.requests.Request()
#     creds.refresh(auth_req)
#     return creds.token


# # ----------------------------
# # Chainlit Lifecycle
# # ----------------------------
# @cl.on_chat_start
# async def start():
#     # Identify user
#     user = cl.user_session.get("user")
#     user_id = user.identifier if user else "logistics-manager"
#     cl.user_session.set("user_id", user_id)

#     # Welcome UI
#     welcome_html = (
#         '<div class="scmgpt-container">'
#         '<div class="header-profile"><span class="model-info">Model: SupplyChain-v4.2 (Streaming Engine)</span></div>'
#         '<div class="welcome-area">'
#         '<h1>Hello, <span class="highlight">Logistics Manager</span></h1>'
#         '<p class="sub-headline">Ask me anything. Say “show map” to render the latest uploaded map from GCS.</p>'
#         "</div>"
#         "</div>"
#     )
#     await cl.Message(content=welcome_html).send()
    
#     # ---------------------------------------------------------
#     # 1) Secure Session Handshake
#     # ---------------------------------------------------------
#     init_msg = cl.Message(content="⏳ *Initializing secure connection to Agent Engine...*")
#     await init_msg.send()
    
#     payload = {
#         "class_method": "async_create_session",
#         "input": {"user_id": user_id}
#     }
    
#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=30.0) as client:
#             resp = await client.post(AGENT_ENGINE_QUERY_URL, json=payload, headers=headers)
            
#             if resp.status_code != 200:
#                 init_msg.content = f"❌ **Backend session creation failed ({resp.status_code}):**\n{resp.text}"
#                 await init_msg.update()
#                 return
                
#             data = resp.json()
#             session_data = data.get("output", {})
#             backend_session_id = session_data.get("id") or session_data.get("session_id")
            
#             if not backend_session_id:
#                 raise ValueError(f"Could not find a valid session ID in the backend response: {data}")

#             # Store the OFFICIAL backend session ID
#             cl.user_session.set("session_id", backend_session_id)
#             logger.info("Successfully created backend session: user=%s session=%s", user_id, backend_session_id)
            
#             init_msg.content = "✅ **Connection verified.** Ready for your queries."
#             await init_msg.update()
            
#     except Exception as e:
#         logger.error(f"Session initialization error: {e}")
#         init_msg.content = f"❌ **Critical Error initializing backend session:** {str(e)}"
#         await init_msg.update()


# @cl.on_message
# async def main(message: cl.Message):
#     user_text = message.content.strip()
#     user_id = cl.user_session.get("user_id")
#     session_id = cl.user_session.get("session_id")
    
#     if not user_id or not session_id:
#         await cl.Message(content="⚠️ No active session. Please refresh the app to reconnect.").send()
#         return

#     msg = cl.Message(content="")
#     await msg.send()

#     # ---------------------------------------------------------
#     # 2) Stream using the official Session ID
#     # ---------------------------------------------------------
#     payload = {
#         "class_method": "async_stream_query",
#         "input": {
#             "user_id": user_id,
#             "session_id": session_id,
#             "message": user_text 
#         }
#     }

#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json",
#         "Accept": "text/event-stream"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=120.0) as client:
#             async with client.stream("POST", AGENT_ENGINE_STREAM_URL, json=payload, headers=headers) as response:
                
#                 if response.status_code != 200:
#                     error_data = await response.aread()
#                     msg.content = f"❌ **API Error {response.status_code}:**\n{error_data.decode()}"
#                     await msg.update()
#                     return

#                 async for line in response.aiter_lines():
#                     line = line.strip()
#                     if not line:
#                         continue
                        
#                     if line.startswith("data:"):
#                         data_str = line[5:].strip()
#                     else:
#                         data_str = line
                        
#                     if data_str == "[DONE]":
#                         continue
                        
#                     try:
#                         chunk = json.loads(data_str)
#                         content = chunk.get("content")
                        
#                         if isinstance(content, str):
#                             await msg.stream_token(content)
                            
#                         elif isinstance(content, dict):
#                             parts = content.get("parts", [])
#                             for part in parts:
#                                 if "text" in part:
#                                     await msg.stream_token(part["text"])
#                                 elif "function_call" in part:
#                                     func_name = part["function_call"].get("name", "tool")
#                                     await msg.stream_token(f"\n> ⚙️ *Executing subagent/tool: {func_name}...*\n")
                                    
#                     except json.JSONDecodeError:
#                         logger.warning(f"⚠️ JSON Decode Error on chunk: {data_str}")
#                         continue

#     except Exception as e:
#         msg.content += f"\n\n⚠️ **Client Error:** {str(e)}"
#         logger.error(f"EXCEPTION: {str(e)}")

#     finally:
#         await msg.update()

#     # ---------------------------------------------------------
#     # 3) Render Maps if Requested
#     # ---------------------------------------------------------
#     if wants_map(user_text):
#         await cl.Message(content="🗺️ Fetching latest map from GCS…").send()
#         try:
#             url = generate_signed_map_url(GCS_BUCKET, GCS_OBJECT, ttl_min=SIGNED_URL_TTL_MIN)
#             await render_map(url, title="Latest Uploaded Map (GCS)")
#         except Exception as e:
#             await cl.Message(content=f"❌ Failed to generate signed map URL: {e}").send()




# import os
# import json
# import httpx
# import logging
# import chainlit as cl
# import google.auth
# import google.auth.transport.requests

# # ----------------------------
# # Vertex AI Configuration
# # ----------------------------
# PROJECT_ID = "saas-poc-env"
# LOCATION = "us-central1"
# ENGINE_ID = "3887944034315927552"

# API_ENDPOINT = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}:streamQuery?alt=sse"

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("scm-gpt")

# # ----------------------------
# # Helper: Authentication
# # ----------------------------
# def get_bearer_token():
#     creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
#     auth_req = google.auth.transport.requests.Request()
#     creds.refresh(auth_req)
#     return creds.token

# # ----------------------------
# # Chainlit Events
# # ----------------------------
# @cl.on_chat_start
# async def start():
#     cl.user_session.set("user_id", "user-scm-test")
#     await cl.Message(content="🚀 **SCM-GPT Online.** Connection verified. Ready to analyze financial subagents...").send()

# @cl.on_message
# async def main(message: cl.Message):
#     user_text = message.content.strip()
#     user_id = cl.user_session.get("user_id")
    
#     msg = cl.Message(content="")
#     await msg.send()

#     payload = {
#         "class_method": "async_stream_query",
#         "input": {
#             "user_id": user_id,
#             "message": user_text 
#         }
#     }

#     headers = {
#         "Authorization": f"Bearer {get_bearer_token()}",
#         "Content-Type": "application/json",
#         "Accept": "text/event-stream"
#     }

#     try:
#         async with httpx.AsyncClient(timeout=120.0) as client:
#             async with client.stream("POST", API_ENDPOINT, json=payload, headers=headers) as response:
                
#                 if response.status_code != 200:
#                     error_data = await response.aread()
#                     msg.content = f"❌ **API Error {response.status_code}:**\n{error_data.decode()}"
#                     await msg.update()
#                     return

#                 async for line in response.aiter_lines():
#                     # 1. Strip whitespace
#                     line = line.strip()
#                     if not line:
#                         continue
                        
#                     # 2. DEBUG: Print every single line received to your terminal
#                     print(f"RAW LINE RECEIVED: {line}")
                        
#                     # 3. Handle both SSE ("data: {...}") and JSONL ("{...}")
#                     if line.startswith("data:"):
#                         data_str = line[5:].strip()
#                     else:
#                         data_str = line
                        
#                     if data_str == "[DONE]":
#                         continue
                        
#                     try:
#                         chunk = json.loads(data_str)
                        
#                         # 4. Extract content based on your specific logs
#                         content = chunk.get("content")
                        
#                         if isinstance(content, str):
#                             # The model is sending raw text
#                             await msg.stream_token(content)
#                         elif isinstance(content, dict):
#                             # The model is sending structured parts
#                             parts = content.get("parts", [])
#                             for part in parts:
#                                 if "text" in part:
#                                     await msg.stream_token(part["text"])
#                                 elif "function_call" in part:
#                                     # Let the user know the agent is thinking/calling a subagent
#                                     func_name = part["function_call"].get("name", "tool")
#                                     await msg.stream_token(f"\n> ⚙️ *Executing subagent: {func_name}...*\n")
                                    
#                     except json.JSONDecodeError:
#                         print(f"⚠️ JSON DECODE ERROR ON: {data_str}")
#                         continue

#     except Exception as e:
#         msg.content += f"\n\n⚠️ **Client Error:** {str(e)}"
#         print(f"EXCEPTION: {str(e)}")

#     finally:
#         # Ensure the message is always finalized
#         await msg.update()
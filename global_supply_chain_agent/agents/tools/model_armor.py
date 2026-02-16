# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
import json
from typing import Any

# --- Safe Imports ---
try:
    import google.auth
    import google.cloud.dlp_v2
    from google.cloud.dlp_v2.types import (
        DeidentifyContentRequest,
        ContentItem,
        InspectConfig,
        InfoType,
        DeidentifyConfig,
        InfoTypeTransformations,
        PrimitiveTransformation,
        ReplaceWithInfoTypeConfig
    )
    _DLP_AVAILABLE = True
except ImportError as e:
    logging.warning(f"⚠️ Google Cloud DLP library not found: {e}. DLP features will be disabled.")
    _DLP_AVAILABLE = False

try:
    from google.cloud import modelarmor_v1
    _ARMOR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"⚠️ Model Armor library not found: {e}. Armor features will be disabled.")
    _ARMOR_AVAILABLE = False

try:
    from google.adk.models import LlmRequest, LlmResponse
    from google.adk.tools import ToolContext, BaseTool
    from google.genai import types
except ImportError as e:
    logging.error(f"❌ Critical: Google ADK or GenAI libraries missing: {e}")
    raise e

# Import your config variables
try:
    from ..config import PROJECT_ID, LOCATION, MODEL_ARMOR_TEMPLATE_ID
except ImportError:
    # Fallbacks for testing if config is missing
    logging.warning("⚠️ Config import failed. Using defaults/environment variables.")
    import os
    PROJECT_ID = os.getenv("PROJECT_ID")
    LOCATION = os.getenv("LOCATION")
    MODEL_ARMOR_TEMPLATE_ID = os.getenv("MODEL_ARMOR_TEMPLATE_ID")

# --- GLOBAL SECURITY CONFIGURATION ---

# !!! "MALFORMED PARENT"  !!!
FALLBACK_PROJECT_ID = PROJECT_ID 

SAFE_PROJECT_ID = str(PROJECT_ID).strip()
SAFE_LOCATION = str(LOCATION).strip()

if SAFE_PROJECT_ID.isdigit():
    logging.warning(f"⚠️ Detected Numeric Project ID ({SAFE_PROJECT_ID}). Swapping to Fallback ID for DLP compatibility.")
    SAFE_PROJECT_ID = FALLBACK_PROJECT_ID

# Construct Resources
DLP_PARENT_RESOURCE = f"projects/{SAFE_PROJECT_ID}/locations/{SAFE_LOCATION}"
MODEL_ARMOR_TEMPLATE_PATH = f"projects/{SAFE_PROJECT_ID}/locations/{SAFE_LOCATION}/templates/{MODEL_ARMOR_TEMPLATE_ID}"

logging.info(f"[Security Config] Using Project ID: '{SAFE_PROJECT_ID}'")
logging.info(f"[Security Config] Using Modal armor template: '{MODEL_ARMOR_TEMPLATE_PATH}'")
logging.info(f"[Security Config] DLP Parent: '{DLP_PARENT_RESOURCE}'")

_dlp_client = None
_armor_client = None

# --- Initialize Clients ---
def get_dlp_client():
    global _dlp_client
    if not _DLP_AVAILABLE:
        return None
        
    if _dlp_client is None:
        try:
            _dlp_client = google.cloud.dlp_v2.DlpServiceClient()
            logging.info("[Security Init] ✅ DLP Client initialized.")
        except Exception as e:
            logging.error(f"[Security Init] ❌ Failed to initialize DLP: {e}")
            _dlp_client = None
    return _dlp_client

def get_armor_client():
    global _armor_client
    if not _ARMOR_AVAILABLE:
        return None

    if _armor_client is None:
        try:
            # Ensure the endpoint matches your location
            api_endpoint = f"modelarmor.{SAFE_LOCATION}.rep.googleapis.com"
            _armor_client = modelarmor_v1.ModelArmorClient(
                client_options={"api_endpoint": api_endpoint}
            )
            logging.info(f"[Security Init] ✅ Model Armor Client initialized for {SAFE_LOCATION}.")
        except Exception as e:
            logging.error(f"[Security Init] ❌ Failed to initialize Model Armor: {e}")
            _armor_client = None
    return _armor_client

# --- DLP CONFIG ---
BUILT_IN_INFO_TYPES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD_NUMBER", "IP_ADDRESS"]
CUSTOM_REGEX_PATTERNS = {"USER_ID_PATTERN": r"user_[a-zA-Z0-9_]+"}

def deidentify_text_with_dlp(text: str) -> str:
    # 1. Input Validation
    if not text or not isinstance(text, str):
        return text
    
    client = get_dlp_client()
    if not client:
        return text

    # 2. Configuration
    try:
        inspect_config = InspectConfig(
            info_types=[InfoType(name=i) for i in BUILT_IN_INFO_TYPES],
            custom_info_types=[
                {"info_type": {"name": k}, "regex": {"pattern": v}}
                for k, v in CUSTOM_REGEX_PATTERNS.items()
            ]
        )

        deidentify_config = DeidentifyConfig(
            info_type_transformations=InfoTypeTransformations(transformations=[
                InfoTypeTransformations.InfoTypeTransformation(
                    primitive_transformation=PrimitiveTransformation(
                        replace_with_info_type_config=ReplaceWithInfoTypeConfig()
                    )
                )
            ])
        )

        # 3. Execution with Specific Error Handling
        response = client.deidentify_content(
            request=DeidentifyContentRequest(
                parent=DLP_PARENT_RESOURCE,
                inspect_config=inspect_config,
                deidentify_config=deidentify_config,
                item=ContentItem(value=text)
            )
        )
        return response.item.value

    except google.api_core.exceptions.InvalidArgument as e:
        # This catches the 400 "Malformed parent" error specifically
        logging.error(f"❌ DLP Configuration Error (Check Project ID format): {e}")
        return text
    except Exception as e:
        logging.error(f"❌ General DLP Error: {e}")
        return text

# --- INPUT GUARDRAIL ---
def check_model_input(llm_request: LlmRequest, **kwargs):
    try:
        armor = get_armor_client()
        if not llm_request.contents:
            return

        last_item = llm_request.contents[-1]
        if not getattr(last_item, 'parts', None):
            return

        # 1. Redact input first (DLP)
        redacted_parts = []
        aggregate_text = []
        for p in last_item.parts:
            txt = getattr(p, 'text', None)
            if txt:
                red_text = deidentify_text_with_dlp(txt)
                redacted_parts.append(types.Part.from_text(text=red_text))
                aggregate_text.append(red_text)
            else:
                redacted_parts.append(p)
        
        # Apply redaction to the request object immediately
        last_item.parts = redacted_parts

        if not armor or not aggregate_text:
            return

        # 2. Check against Model Armor Policy
        sanitized_text = "\n".join(aggregate_text)
        
        req = modelarmor_v1.SanitizeUserPromptRequest(
            name=MODEL_ARMOR_TEMPLATE_PATH,
            user_prompt_data=modelarmor_v1.DataItem(text=sanitized_text)
        )
        resp = armor.sanitize_user_prompt(request=req)

        # 3. Handle Violation with Logging
        if resp.sanitization_result.filter_match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
            # Extract specific filter details for logging
            filter_results = resp.sanitization_result.filter_results
            # Convert protobuf map to dict for readable printing
            violation_details = {k: v for k, v in filter_results.items()}
            
            print(f"\n[Security] 🛡️ INPUT BLOCKED: {violation_details}")
            logging.warning(f"[Security] Input Blocked Details: {violation_details}")
            
            # Replace content with system rejection
            last_item.parts = [types.Part.from_text(text="System: Input blocked due to policy.")]
            
    except Exception as e:
        logging.error(f"Armor Input Guardrail Skipped due to error: {e}")

# --- OUTPUT GUARDRAIL ---
def check_model_output(llm_response: LlmResponse, **kwargs) -> LlmResponse:
    try:
        content = llm_response.content
        if not content or not getattr(content, 'parts', None):
            return llm_response

        redacted_parts = []
        aggregate_text = []
        for p in content.parts:
            txt = getattr(p, 'text', None)
            if txt:
                red_text = deidentify_text_with_dlp(txt)
                redacted_parts.append(types.Part.from_text(text=red_text))
                aggregate_text.append(red_text)
            else:
                redacted_parts.append(p)
        content.parts = redacted_parts

        armor = get_armor_client()
        if armor and aggregate_text:
            txt = "\n".join(aggregate_text)
            req = modelarmor_v1.SanitizeModelResponseRequest(
                name=MODEL_ARMOR_TEMPLATE_PATH,
                model_response_data=modelarmor_v1.DataItem(text=txt)
            )
            resp = armor.sanitize_model_response(request=req)
            if resp.sanitization_result.filter_match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                return LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="[Safety Filter] Response removed.")]
                    )
                )
    except Exception as e:
        logging.error(f"Armor Output Guardrail Skipped due to error: {e}")

    return llm_response

# --- TOOL GUARDRAIL ---
def check_tool_output(tool: BaseTool, tool_context: ToolContext, **kwargs) -> Any:
    try:
        raw = kwargs.get('tool_response') or kwargs.get('result') or kwargs.get('output')
        if raw is None:
            return "null"

        actual = getattr(raw, 'output', None) or getattr(raw, 'result', None) or raw

        # Recursively redact strings
        def recurse(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: recurse(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [recurse(v) for v in obj]
            if isinstance(obj, str):
                return deidentify_text_with_dlp(obj)
            return obj

        safe_obj = recurse(actual)
        json_text = json.dumps(safe_obj, ensure_ascii=False)

        armor = get_armor_client()
        if armor:
            req = modelarmor_v1.SanitizeModelResponseRequest(
                name=MODEL_ARMOR_TEMPLATE_PATH,
                model_response_data=modelarmor_v1.DataItem(text=json_text)
            )
            resp = armor.sanitize_model_response(request=req)
            if resp.sanitization_result.filter_match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                return json.dumps({"error": "Tool output blocked by policy."})

        return json_text
        
    except Exception as e:
        logging.error(f"Tool Guardrail Error: {e}")
        return json.dumps({"error": "Guardrail processing failed"})

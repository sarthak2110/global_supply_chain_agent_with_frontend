# export PROJECT="easysaas-adc-demo"
# export LOCATION="us-central1"
# export PROJECT_ID="easysaas-adc-demo"
# export STAGING_BUCKET="gs://londonagent-repo-test"
# gcloud auth application-default login

import os


os.environ.setdefault("PROJECT", "saas-poc-env")
os.environ.setdefault("LOCATION", "us-central1")
os.environ.setdefault("STAGING_BUCKET", "gs://saas_agent_bucket")

# --- Imports ---

from agent.agent import root_agent
# from ..global_supply_chain_agent.agent 
from vertexai.preview import reasoning_engines
from vertexai import agent_engines
import vertexai

# --- User Variables ---
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("LOCATION")
STAGING_BUCKET = os.getenv("STAGING_BUCKET")

# --- Initialize Vertex AI ---
vertexai.init(
    project=PROJECT_ID,
    location=LOCATION,
    staging_bucket=STAGING_BUCKET,
)

# --- ADK APP SECTION (Commented out to fix Version Error) ---
# This block is only for local tracing and causes a crash with google-adk > 1.0.0.
# Since we are deploying to the cloud (AgentEngine.create), we can safely skip it.
# adk_app = reasoning_engines.AdkApp(
#     agent=root_agent,
#     enable_tracing=True,
# )

print(f"Current Directory: {os.getcwd()}")

# --- Deploy to Google Cloud ---
try:
    remote_agent = agent_engines.AgentEngine.create(
        agent_engine=root_agent,                              
        requirements="./requirements.txt",
        extra_packages=["./agent"],
        display_name="global supply chain agent V2",
        description="deployed by agent_engine_deploy.py",
        env_vars={
            "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "TRUE",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "TRUE",
            "MAPS_GCS_BUCKET": "sarthak-test",
            "GOOGLE_MAPS_API_KEY":"",
            # Pass these to the remote container as well just in case
            "PROJECT_ID": 'saas-poc-env',
            "LOCATION": 'us-central1',
        }
    )
    print("✅ Deployment successful!")
    print(f"Agent Resource Name: {remote_agent.resource_name}")
except Exception as e:
    print(f"❌ Deployment failed: {e}")


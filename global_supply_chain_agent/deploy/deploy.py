# export PROJECT="adc-dev-478120"
# export LOCATION="us-central1"
# export PROJECT_ID="adc-staging-478120"
# export STAGING_BUCKET="gs://testing-london-adc"


import os

# --- Imports ---
from london_agent.agent import root_agent
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


print(f"Current Directory: {os.getcwd()}")

# --- Deploy to Google Cloud ---
try:
    remote_agent = agent_engines.AgentEngine.create(
        agent_engine=root_agent,                              
        requirements="./requirements.txt",
        extra_packages=["./london_agent"],
        display_name="London_manually",
        description="deployed by deploy.py",
        env_vars={
            "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
            "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "TRUE",
            "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "TRUE",
            "MODEL_ARMOR_TEMPLATE_ID": "TravelApp_Armor",
            "DB_TYPE": "sqlite",
            # Pass these to the remote container as well just in case
            "PROJECT_ID": PROJECT_ID,
            "LOCATION": LOCATION,
            "BIGQUERY_PROJECT_ID":PROJECT_ID
        }
    )
    print("✅ Deployment successful!")
    print(f"Agent Resource Name: {remote_agent.resource_name}")
except Exception as e:
    print(f"❌ Deployment failed: {e}")
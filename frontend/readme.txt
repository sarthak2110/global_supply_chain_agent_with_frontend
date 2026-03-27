gcloud services enable iamcredentials.googleapis.com --project saas-poc-env


gsutil iam ch \
  "serviceAccount:infra-manager-sa@saas-poc-env.iam.gserviceaccount.com:objectViewer" \
  gs://sarthak-test


gcloud auth application-default login \
  --impersonate-service-account="infra-manager-sa@saas-poc-env.iam.gserviceaccount.com"

export GCS_BUCKET="sarthak-test"
export GCS_OBJECT="maps/route_map.html"
export SIGNED_URL_TTL_MIN="30"




cd google-next-26/frontend
source .venv/bin/activate
chainlit run app.py -w --port 8501
chainlit run app2-test.py -w --port 8501

chainlit run app.py -w
or
chainlit run app.py -w --port 8501


deployment:

cloud run:
export _REGION="us-central1"
export _PREFIX="globalsupply-chain-frontend"
export _projectID="saas-poc-env"
export _version="v1.02.0"

gcloud artifacts repositories create ${_PREFIX} --repository-format=docker --location=$_REGION --project=${_projectID}
docker build  --tag ${_REGION}-docker.pkg.dev/${_projectID}/${_PREFIX}/${_version}:latest .
docker push ${_REGION}-docker.pkg.dev/${_projectID}/${_PREFIX}/${_version}:latest

variables:
AGENT_ENGINE_QUERY_URL=https://us-central1-aiplatform.googleapis.com/v1/projects/saas-poc-env/locations/us-central1/reasoningEngines/1801053372611035136:query
AGENT_ENGINE_STREAM_URL=https://us-central1-aiplatform.googleapis.com/v1/projects/saas-poc-env/locations/us-central1/reasoningEngines/1801053372611035136:streamQuery?alt=sse
GCS_BUCKET=sarthak-test
GCS_OBJECT=maps/route_map.html
SIGNED_URL_TTL_MIN=30




Permissions on Agent engine SA:
BigQuery Admin
Developer Connect Read Token Accessor (Beta)
Developer Connect Read Token Accessor (Beta)
Model Armor Admin
Storage Admin
Vertex AI Reasoning Engine Service Agent
Vertex AI Reasoning Engine Service Agent
Vertex AI User
Vertex AI User
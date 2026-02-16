gcloud services enable iamcredentials.googleapis.com --project saas-poc-env


gsutil iam ch \
  "serviceAccount:infra-manager-sa@saas-poc-env.iam.gserviceaccount.com:objectViewer" \
  gs://sarthak-test


gcloud auth application-default login \
  --impersonate-service-account="infra-manager-sa@saas-poc-env.iam.gserviceaccount.com"

export GCS_BUCKET="sarthak-test"
export GCS_OBJECT="maps/route_map.html"
export SIGNED_URL_TTL_MIN="30"



chainlit run app.py -w
or
chainlit run app.py -w --port 8501
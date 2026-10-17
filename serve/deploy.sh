#!/usr/bin/env bash
# Deploy Abiso serve/ to Cloud Run on TMUC's GCP project (credits: billing acct ...-4A71C9).
#
# PAID ACTION. Without ABISO_DEPLOY_APPROVED=yes this only prints the plan and exits 2.
# PUBLIC since 2026-10-17 via --no-invoker-iam-check (the TMUC org's domain-restricted sharing
# refuses an allUsers binding, so --allow-unauthenticated cannot work here) (the AI Builder Cup needs a link judges can open): the page and
# /api are open; /api/ask is rate-limited per caller and the agent stops Gemini past
# ABISO_MAX_USD per instance; /tasks/refresh needs the X-Abiso-Task secret.
# Gemini runs on Vertex AI in this project (billed to the GCP credits, no API key).
#
#   serve/deploy.sh                                        # dry run
#   ABISO_DEPLOY_APPROVED=yes serve/deploy.sh              # deploy with data/events.jsonl
#   ABISO_DATA=fixture ABISO_DEPLOY_APPROVED=yes serve/deploy.sh   # deploy on the SYNTHETIC fixture
#
# Uses the isolated TMUC gcloud login (~/.config/carl-life-os/gcloud-tmuc). If gcloud
# says the credential is expired:  CLOUDSDK_CONFIG=$HOME/.config/carl-life-os/gcloud-tmuc gcloud auth login
set -euo pipefail

export CLOUDSDK_CONFIG="${CLOUDSDK_CONFIG:-$HOME/.config/carl-life-os/gcloud-tmuc}"
PROJECT="${ABISO_GCP_PROJECT:-dev-antler-508423-m1}"
REGION="${ABISO_REGION:-asia-southeast1}"          # Singapore: closest Cloud Run region to Cebu
SERVICE=abiso
REPO=abiso
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/$SERVICE:$(date +%Y%m%d-%H%M%S)"
CALENDAR_ID="${ABISO_CALENDAR_ID:-c_60e2a40f1f1cc55c1536056803a691eec9fda116b1d675f7fe15f64e5192fa96@group.calendar.google.com}"
MAX_USD="${ABISO_MAX_USD:-2.00}"                    # per instance; the meter stops Gemini calls past it
REFRESH_EVERY="${ABISO_REFRESH_CRON:-15 */3 * * *}"   # Cloud Scheduler, Asia/Manila
MIN_INSTANCES="${ABISO_MIN_INSTANCES:-1}"            # 1 during judging: no cold start (~\$10/month idle)
DATA="${ABISO_DATA:-real}"
MESSENGER_SECRETS=(abiso-messenger-verify-token:MESSENGER_VERIFY_TOKEN
                   abiso-messenger-app-secret:MESSENGER_APP_SECRET
                   abiso-messenger-page-token:MESSENGER_PAGE_TOKEN)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "deploy.sh: $*" >&2; exit 1; }

if [[ "$DATA" == fixture ]]; then
  EVENTS=/app/serve/fixtures/events.jsonl POSTS=/app/serve/fixtures/posts.jsonl
else
  [[ -f data/events.jsonl ]] || die "data/events.jsonl missing: run the compiler first, or ABISO_DATA=fixture"
  [[ -f data/raw/posts.jsonl ]] || die "data/raw/posts.jsonl missing: needed for source links"
  EVENTS=/app/data/events.jsonl POSTS=/app/data/raw/posts.jsonl
fi

cat <<EOF
Plan
  project   $PROJECT   (billing must be TMUC ...-4A71C9)
  region    $REGION
  image     $IMAGE
  service   $SERVICE   (PUBLIC page + API; refresh hook needs a secret)
  scaling   min $MIN_INSTANCES, max 3 instances, 1Gi, timeout 900s
  gemini    Vertex AI (global), budget \$$MAX_USD per instance, every call logged with its cost
  refresh   Cloud Scheduler "$REFRESH_EVERY" Asia/Manila -> POST /tasks/refresh
  store     Firestore (default) database
  data      $DATA ($EVENTS)
  calendar  $CALENDAR_ID
EOF

if [[ "${ABISO_DEPLOY_APPROVED:-}" != "yes" ]]; then
  echo
  echo "Dry run only. Deploying is a paid action: re-run with ABISO_DEPLOY_APPROVED=yes after Carl approves."
  exit 2
fi

billing=$(gcloud billing projects describe "$PROJECT" --format='value(billingAccountName)')
[[ "$billing" == *4A71C9 ]] || die "project $PROJECT bills to '$billing', not TMUC's ...-4A71C9; refusing"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com aiplatform.googleapis.com \
  cloudscheduler.googleapis.com --project "$PROJECT"

# The scheduler proves itself to /tasks/refresh with this random token (made once, never printed).
if ! gcloud secrets describe abiso-task-token --project "$PROJECT" >/dev/null 2>&1; then
  openssl rand -hex 32 | tr -d '\n' | gcloud secrets create abiso-task-token --data-file=- --project "$PROJECT"
fi
secrets="ABISO_TASK_TOKEN=abiso-task-token:latest"
for pair in "${MESSENGER_SECRETS[@]}"; do
  name=${pair%%:*} var=${pair##*:}
  if gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    secrets+=",$var=$name:latest"
  else
    echo "note: secret $name not found; the Messenger webhook will answer 503 until it exists"
  fi
done

# Cloud Run runs as the default compute service account; it must read the secrets.
number=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
runtime_sa="$number-compute@developer.gserviceaccount.com"
for s in $(echo "$secrets" | tr ',' '\n' | sed 's/.*=\(.*\):latest/\1/'); do
  gcloud secrets add-iam-policy-binding "$s" --project "$PROJECT" \
    --member "serviceAccount:$runtime_sa" --role roles/secretmanager.secretAccessor >/dev/null
done

# The runtime reads and writes Firestore and calls Gemini on Vertex AI.
for role in roles/datastore.user roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$runtime_sa" \
    --role "$role" --condition None >/dev/null
done

# New projects run Cloud Build as the same compute account, which starts without build rights.
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$runtime_sa" \
  --role roles/cloudbuild.builds.builder --condition None >/dev/null

gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" --repository-format docker \
       --location "$REGION" --project "$PROJECT"

gcloud builds submit . --project "$PROJECT" --region "$REGION" --config serve/cloudbuild.yaml \
  --ignore-file serve/.gcloudignore --substitutions "_IMAGE=$IMAGE"

gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --image "$IMAGE" \
  --no-invoker-iam-check --min-instances "$MIN_INSTANCES" --max-instances 3 --memory 1Gi \
  --timeout 900 --set-secrets "$secrets" \
  --set-env-vars "ABISO_CALENDAR_ID=$CALENDAR_ID,ABISO_EVENTS=$EVENTS,ABISO_POSTS=$POSTS,ABISO_MAX_USD=$MAX_USD,ABISO_STORE=firestore,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,ABISO_REFRESH_RUN_USD=0.15,ABISO_REFRESH_TOTAL_USD=10"

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')
# The A2A agent card advertises this URL, so set it now that it is known.
gcloud run services update "$SERVICE" --project "$PROJECT" --region "$REGION" \
  --update-env-vars "ABISO_PUBLIC_URL=$URL"

code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/")
[[ "$code" == 200 ]] || die "the public page answered $code, not 200"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/tasks/refresh")
[[ "$code" == 403 ]] || die "/tasks/refresh without the token answered $code, not 403"
curl -fsS "$URL/health" || die "deployed, but $URL/health failed"

# Cloud Scheduler: refresh the feed every few hours (create once, update after).
token=$(gcloud secrets versions access latest --secret abiso-task-token --project "$PROJECT")
job=(--project "$PROJECT" --location "$REGION" --schedule "$REFRESH_EVERY" --time-zone Asia/Manila
     --uri "$URL/tasks/refresh" --http-method POST --attempt-deadline 900s --max-retry-attempts 1)
if gcloud scheduler jobs describe abiso-refresh --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http abiso-refresh "${job[@]}" --update-headers "X-Abiso-Task=$token" >/dev/null
else
  gcloud scheduler jobs create http abiso-refresh "${job[@]}" --headers "X-Abiso-Task=$token" >/dev/null
fi
unset token
echo
echo "Deployed (public): $URL"
echo "  page:     $URL/"
echo "  MCP:      $URL/mcp/"
echo "  A2A card: $URL/a2a/.well-known/agent-card.json"
echo "  refresh:  gcloud scheduler jobs run abiso-refresh --project $PROJECT --location $REGION"

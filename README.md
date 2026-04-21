# Cloud Run Remediation Agent

An ADK agent that listens for Cloud Run error logs, diagnoses the root cause, and automatically remediates — deployed as a Cloud Run Service triggered by Eventarc.

## How it works

1. Cloud Run services emit error logs to Cloud Logging
2. A Logging sink filters `severity>=ERROR` logs and routes them to a Pub/Sub topic
3. Eventarc delivers the event as an HTTP POST to this Cloud Run Service
4. The agent reads the error, inspects the affected service, and takes action (rollback, env var fix, etc.)
5. Results are logged to Cloud Logging

## Project structure

```
├── main.py              # Service entrypoint — receives Eventarc HTTP POST, runs agent
├── runner.py            # ADK Runner + session service
├── agent.py             # LlmAgent definition, loads skill from file
├── tools.py             # Cloud Run v2 API tools (list/get/rollback/update)
├── skills/
│   └── remediation/
│       ├── SKILL.md     # Agent playbook — edit this to change behavior
│       └── scripts/     # Shell scripts for the code-fix track
│           ├── clone_repo.sh
│           ├── read_file.sh
│           ├── apply_fix.sh
│           ├── commit_branch.sh
│           ├── open_pr.sh
│           └── rollback_fix.sh
├── requirements.txt
└── Dockerfile
```

### Remediation tracks

| Track | Trigger | Tools used |
|---|---|---|
| **Infra fix** | OOM, CPU throttle, bad deploy, missing env var | `_get_service`, `_update_service_resources`, `_rollback_traffic`, `_update_service_env_vars` |
| **Code fix** | Application bug in source (stack trace, import error, logic bug) | `_clone_repo` → `_read_repo_file` → `_apply_code_fix` → `_commit_to_incident_branch` → `_open_pull_request` |
| **Code fix rollback** | Undo a code fix PR (demo / wrong fix) | `_rollback_fix` |

The code-fix track creates a branch named `incident_YYMMDDHH` (from the error log timestamp), commits the fix, pushes, and opens a PR. To roll back, call `_rollback_fix` with the branch name — it closes the PR and deletes the branch.

---

## Prerequisites

### GCP setup

1. **Enable APIs**
   ```bash
   gcloud services enable run.googleapis.com eventarc.googleapis.com pubsub.googleapis.com aiplatform.googleapis.com logging.googleapis.com
   ```

2. **Create a service account** for the agent
   ```bash
   gcloud iam service-accounts create remediation-agent \
     --display-name="Cloud Run Remediation Agent"
   ```

3. **Grant IAM roles** to the service account
   ```bash
   PROJECT_ID=$(gcloud config get-value project)
   SA="remediation-agent@${PROJECT_ID}.iam.gserviceaccount.com"

   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA}" --role="roles/run.admin"

   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA}" --role="roles/iam.serviceAccountUser"

   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA}" --role="roles/eventarc.eventReceiver"

   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA}" --role="roles/aiplatform.user"

   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA}" --role="roles/artifactregistry.reader"
   ```

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Yes | — | GCP project ID |
| `CLOUD_RUN_REGION` | No | `us-central1` | Region where your services live |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | — | Set to `True` to route LLM calls through Vertex AI |
| `GITHUB_REPO_URL` | For code-fix track | — | HTTPS URL of the repo to clone, e.g. `https://github.com/org/repo` |
| `GITHUB_TOKEN` | For code-fix track | — | GitHub PAT — set directly for local dev; in production use Secret Manager (see below) |
| `GITHUB_TOKEN_SECRET` | For code-fix track | — | Secret Manager resource name — used instead of `GITHUB_TOKEN` in production |
| `GIT_AUTHOR_NAME` | No | `DinoAgent` | Git commit author name |
| `GIT_AUTHOR_EMAIL` | No | `dinoagent@noreply.github.com` | Git commit author email |

---

## GitHub token setup (code-fix track)

The code-fix track needs a GitHub Personal Access Token (PAT) with `repo` scope.

### Local dev

Add to `.env`:
```
GITHUB_TOKEN=ghp_xxxx
GITHUB_REPO_URL=https://github.com/org/repo
```

### Production — Secret Manager (recommended)

**One-time secret creation:**
```bash
echo -n "ghp_YOUR_GITHUB_TOKEN_HERE" | gcloud secrets create github-token --data-file=-
```

**Grant the Cloud Run service account access:**
```bash
PROJECT_ID=$(gcloud config get-value project)
SA="remediation-agent@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud secrets add-iam-policy-binding github-token \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor"
```

**Deploy with the secret name as a plain env var** (agent fetches it at startup):
```bash
gcloud run deploy remediation-agent \
  --image=$IMAGE \
  --region=us-central1 \
  --service-account=$SA \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=True" \
  --set-env-vars GITHUB_TOKEN_SECRET=projects/${PROJECT_ID}/secrets/github-token/versions/latest \
  --set-env-vars GITHUB_REPO_URL=https://github.com/org/repo \
  --no-allow-unauthenticated \
  --timeout=300
```

```bash
gcloud services enable secretmanager.googleapis.com
```

---

## Running locally

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Authenticate with GCP**
   ```bash
   gcloud auth application-default login
   ```

3. **Copy and fill in env vars**
   ```bash
   cp .env.example .env
   # edit .env with your project ID
   ```

4. **Run with a test error message**
   ```bash
   ERROR_MESSAGE="Service dinoquest2 is failing: container exited with code 1 after recent deploy" \
   python main.py
   ```

   `ERROR_MESSAGE` bypasses the HTTP server for local testing. In production the message arrives from Eventarc as an HTTP POST.

---

## Deploying to Cloud Run

### 1. Build and push the container image

```bash
PROJECT_ID=$(gcloud config get-value project)
IMAGE="gcr.io/$PROJECT_ID/remediation-agent:latest"

gcloud builds submit --tag $IMAGE .
```

### 2. Deploy the Cloud Run Service

```bash
SA="remediation-agent@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud run deploy remediation-agent \
  --image=$IMAGE \
  --region=us-central1 \
  --service-account=$SA \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=True" \
  --set-env-vars="GITHUB_REPO_URL=https://github.com/org/repo" \
  --set-env-vars GITHUB_TOKEN_SECRET=projects/${PROJECT_ID}/secrets/github-token/versions/latest \
  --no-allow-unauthenticated \
  --timeout=300
```

> No API key needed — the service authenticates to Vertex AI via the attached service account's Application Default Credentials.

### 3. Create a Pub/Sub topic for Cloud Run errors

```bash
gcloud pubsub topics create cloud-run-errors
```

### 4. Create a Cloud Logging sink

Route error logs from Cloud Run to the Pub/Sub topic:

```bash
FILTER='resource.type="cloud_run_revision" resource.labels.service_name="dinoquest2" severity=ERROR NOT logName=~"cloudaudit" NOT httpRequest.requestUrl=~"/_ah/health"'

gcloud logging sinks create cloud-run-errors-sink \
  pubsub.googleapis.com/projects/${PROJECT_ID}/topics/cloud-run-errors \
  --log-filter="$FILTER"
```

> Scope the filter to the specific service name you want to monitor. Adjust `resource.labels.service_name` as needed.

Then grant the sink's writer identity permission to publish to the topic. The writer identity is printed when the sink is created — use the value from that output:

```bash
gcloud pubsub topics add-iam-policy-binding cloud-run-errors \
  --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-logging.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher"
```

Or retrieve it programmatically:

```bash
SINK_SA=$(gcloud logging sinks describe cloud-run-errors-sink --format='value(writerIdentity)')

gcloud pubsub topics add-iam-policy-binding cloud-run-errors \
  --member="${SINK_SA}" --role="roles/pubsub.publisher"
```

**To update the filter on an existing sink:**

```bash
FILTER='resource.type="cloud_run_revision" resource.labels.service_name="dinoquest2" severity=ERROR NOT logName=~"cloudaudit" NOT httpRequest.requestUrl=~"/_ah/health"'

gcloud logging sinks update cloud-run-errors-sink --log-filter="$FILTER"
```

### 5. Grant Eventarc permission to invoke the service

```bash
gcloud run services add-iam-policy-binding remediation-agent \
  --region=us-central1 \
  --member="serviceAccount:${SA}" \
  --role="roles/run.invoker"
```

### 6. Create the Eventarc trigger

```bash
gcloud eventarc triggers create remediation-trigger \
  --location=us-central1 \
  --destination-run-service=remediation-agent \
  --destination-run-region=us-central1 \
  --event-filters="type=google.cloud.pubsub.topic.v1.messagePublished" \
  --transport-topic=projects/${PROJECT_ID}/topics/cloud-run-errors \
  --service-account=${SA}
```

### 7. Verify

```bash
gcloud eventarc triggers describe remediation-trigger --location=us-central1
```

### Updating after code changes

```bash
gcloud builds submit --tag $IMAGE . && \
gcloud run services update remediation-agent --image=$IMAGE --region=us-central1
```

---

## Customizing the agent

Edit `skills/remediation.md` to change the agent's behavior — add new remediation strategies, adjust guardrails, or extend the output format — without touching any Python code. Rebuild and redeploy the image to pick up changes.

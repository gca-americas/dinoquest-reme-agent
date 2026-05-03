# DinoAgent (Cloud Run Remediation Agent)

An ADK agent that listens for Cloud Run error logs, diagnoses the root cause, and automatically remediates — deployed as a Cloud Run Service triggered by Eventarc.

Also acts as the **orchestrator** in the Section 3 agent team: receives chat messages via chat-bridge, delegates CI builds to CIAgent and canary deploys to CDAgent via A2A, and teaches new skills to CIAgent when it encounters unclassified failures (Behavior B).

## Changes from Section 1

Added in the Section 3 refactor:

- `utils.py` — `emit_event()` publishes structured events to the `harness-events` Pub/Sub topic. `resolve_secret()` consolidates the Secret Manager pattern that was duplicated across `main.py` and `agent.py`.
- `main.py` — emits `detected_error` when remediation starts and `thinking` with the abbreviated final response when it ends.
- `agent.py` — each tool closure emits a `thinking` event before executing, so every meaningful action is observable in real time (see table below).
- `requirements.txt` — added `google-cloud-pubsub`.
- New env var: `HARNESS_EVENTS_TOPIC=projects/{project}/topics/harness-events` (optional — events are silently skipped if unset, so Section 1 behavior is unchanged).

The OOM remediation flow, Slack notifications, Eventarc trigger, and all shell scripts are unchanged.

### Pub/Sub events emitted

Every event is published to the `harness-events` topic with `"agent": "DinoAgent"`.

| Source | `event_type` | `payload` fields | dino-theater station |
|---|---|---|---|
| `main.py` — on trigger | `detected_error` | `error_preview` (≤200 chars) | Cloud General |
| `agent.py` — `_rollback_traffic` | `thinking` | `summary`: `Rolling back Cloud Run {svc} → {rev} ({pct}% traffic)` | Cloud Run |
| `agent.py` — `_update_service_env_vars` | `thinking` | `summary`: `Updating Cloud Run {svc} env vars: {keys}` | Cloud Run |
| `agent.py` — `_update_service_resources` | `thinking` | `summary`: `Patching Cloud Run {svc}: memory → {mem}` | Cloud Run |
| `agent.py` — `_apply_code_fix` | `thinking` | `summary`: `Applying fix: editing {file} to resolve root cause` | Source Code |
| `agent.py` — `_commit_to_incident_branch` | `thinking` | `summary`: `Pushing branch: committing fix — {commit_message}` | GitHub |
| `agent.py` — `_open_pull_request` | `thinking` | `summary`: `Opening pull request: {title}` | GitHub |
| `agent.py` — `_rollback_fix` | `thinking` | `summary`: `Rolling back code fix: closing PR and deleting branch {branch}` | GitHub |
| `agent.py` — `_announce_ci_call` | `a2a_call_sent` | `target_agent`: `CIAgent`, `method`: `teach_skill`, `args_preview`: message preview | GitHub |
| `main.py` — on completion | `thinking` | `summary`: LLM final response (≤300 chars) | varies |

## How it works

1. Cloud Run services emit error logs to Cloud Logging
2. A Logging sink filters `severity=ERROR` logs and routes them to a Pub/Sub topic
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
| **OOM root-cause** | Always runs after an OOM infra fix | `_clone_repo` → `_read_repo_file` → `_apply_code_fix` → `_commit_to_incident_branch` → `_open_pull_request` |
| **Code fix** | Application bug in source (stack trace, import error, logic bug) | same as above |
| **Rollback** | Undo a code fix PR (demo / wrong fix) | `_rollback_fix` |

The code-fix track creates a branch named `incident_YYMMDDHH` (from the error log timestamp), commits the fix, pushes, and opens a PR. To roll back, call `_rollback_fix` with the branch name — it closes the PR and deletes the branch.

---

## Prerequisites

### GCP setup

1. **Enable APIs**
   ```bash
   gcloud services enable run.googleapis.com eventarc.googleapis.com pubsub.googleapis.com \
     aiplatform.googleapis.com logging.googleapis.com secretmanager.googleapis.com
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

   gcloud projects add-iam-policy-binding $PROJECT_ID \
     --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
   ```

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Yes | — | GCP project ID |
| `CLOUD_RUN_REGION` | No | `us-central1` | Region where your services live |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | — | Set to `True` to route LLM calls through Vertex AI |
| `GITHUB_REPO_URL` | For code-fix track | — | HTTPS URL of the repo to clone, e.g. `https://github.com/org/repo` |
| `GITHUB_TOKEN` | For code-fix track | — | GitHub PAT — injected via Secret Manager in production (see below) |
| `GIT_AUTHOR_NAME` | No | `DinoAgent` | Git commit author name |
| `GIT_AUTHOR_EMAIL` | No | `dinoagent@noreply.github.com` | Git commit author email |
| `SLACK_WEBHOOK_URL` | For Slack notifications | — | Incoming webhook URL for a Slack channel |
| `SLACK_WEBHOOK_SECRET` | For Slack notifications (prod) | — | Secret Manager resource name, e.g. `projects/my-project/secrets/slack-webhook/versions/latest` |
| `CIAGENT_URL` | For A2A skill teaching | — | Base URL of CIAgent Cloud Run service, e.g. `https://ci-agent-xxx-uc.a.run.app` |
| `HARNESS_EVENTS_TOPIC` | For dino-theater | — | Full Pub/Sub topic resource name, e.g. `projects/{project}/topics/harness-events`. Events silently skipped if unset. |

---

## Slack notifications

When a remediation completes, the agent POSTs a summary to a Slack channel via an incoming webhook.

### 1. Create a Slack incoming webhook

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Pick a name (e.g. `DinoAgent`) and select your workspace → **Create App**.
3. Under **Add features and functionality**, click **Incoming Webhooks** → toggle **Activate Incoming Webhooks** on.
4. Click **Add New Webhook to Workspace**, choose the channel, and click **Allow**.
5. Copy the webhook URL — it looks like:
   ```
   https://hooks.slack.com/services/<team_id>/<channel_id>/<token>
   ```

### 2. Local dev

Add to `.env`:
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 3. Production — Secret Manager (recommended)

**One-time secret creation:**
```bash
echo -n "https://hooks.slack.com/services/..." | gcloud secrets create slack-webhook --data-file=-
```

**Grant the Cloud Run service account access:**
```bash
PROJECT_ID=$(gcloud config get-value project)
SA="remediation-agent@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud secrets add-iam-policy-binding slack-webhook \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor"
```

**Pass the secret name to Cloud Run:**
```bash
gcloud run services update remediation-agent \
  --region=us-central1 \
  --set-env-vars="SLACK_WEBHOOK_SECRET=projects/${PROJECT_ID}/secrets/slack-webhook/versions/latest"
```

Or add `--set-env-vars="SLACK_WEBHOOK_SECRET=..."` directly to the initial `gcloud run deploy` command.

---

## GitHub token setup (code-fix track)

The code-fix and OOM root-cause tracks need a GitHub Personal Access Token (PAT) with `repo` scope.

### Local dev

Add to `.env`:
```
GITHUB_TOKEN=ghp_xxxx
GITHUB_REPO_URL=https://github.com/org/repo
```

### Production — Secret Manager (recommended)

**One-time secret creation:**
```bash
echo -n "ghp_xxxx" | gcloud secrets create github-token --data-file=-
```

**Grant the Cloud Run service account access:**
```bash
PROJECT_ID=$(gcloud config get-value project)
SA="remediation-agent@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud secrets add-iam-policy-binding github-token \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Running locally

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install `git` and `gh`** (required for the code-fix track)
   ```bash
   # macOS
   brew install git gh

   # Debian/Ubuntu
   sudo apt-get install git gh
   ```

3. **Authenticate with GCP**
   ```bash
   gcloud auth application-default login
   ```

4. **Copy and fill in env vars**
   ```bash
   cp .env.example .env
   # edit .env — set GOOGLE_CLOUD_PROJECT, and GITHUB_REPO_URL + GITHUB_TOKEN for code-fix track
   ```

5. **Run with a test error message**

   Set `ERROR_MESSAGE` in `.env` to one of the examples below, then:
   ```bash
   python main.py
   ```
   `ERROR_MESSAGE` bypasses the HTTP server for local testing. In production the message arrives from Eventarc as an HTTP POST.

### Test messages by track

**Infra track — OOM** (also triggers OOM root-cause track if `GITHUB_REPO_URL` is set)
```
{"severity":"ERROR","resource":{"type":"cloud_run_revision","labels":{"service_name":"dinoquest2","revision_name":"dinoquest2-00001-abc"}},"textPayload":"Memory limit of 128 MiB exceeded with 128 MiB used. Consider increasing the memory limit, see https://cloud.google.com/run/docs/configuring/memory-limits","timestamp":"2026-04-17T18:26:06Z"}
```

**Infra track — bad deploy / rollback**
```
{"severity":"ERROR","resource":{"type":"cloud_run_revision","labels":{"service_name":"dinoquest2"}},"textPayload":"Revision dinoquest2-00042-abc failed health check: container failed to start.","timestamp":"2026-04-17T18:26:06Z"}
```

**Code-fix track — application bug** (requires `GITHUB_REPO_URL` and `GITHUB_TOKEN`)
```
{"severity":"ERROR","resource":{"type":"cloud_run_revision","labels":{"service_name":"dinoquest2"}},"textPayload":"Traceback (most recent call last):\n  File \"/app/main.py\", line 42, in handle_request\n    result = process_data(payload)\n  File \"/app/processor.py\", line 17, in process_data\n    return data[\"items\"][0]\nKeyError: \"items\"","timestamp":"2026-04-17T18:26:06Z"}
```

**To test rollback** — after a code-fix run, pass the branch name back:
```
Roll back the fix on branch incident_2604201826 for service dinoquest2
```

### Resetting dedup between test runs

The agent deduplicates events for 5 minutes (in-memory + Firestore). To re-trigger the same error without waiting, flush the dedup state:

```bash
# Local
curl -X POST http://localhost:8080/flush-dedup

# Cloud Run
curl -X POST https://<remediation-agent-url>/flush-dedup \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)"
```

Response:
```json
{"ok": true, "cleared_local": 3, "cleared_firestore": 3}
```

This clears all entries from the in-memory cache and the `remediation_dedup` Firestore collection. The next matching error will be processed as fresh.

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
PROJECT_ID=$(gcloud config get-value project)
SA="remediation-agent@${PROJECT_ID}.iam.gserviceaccount.com"
TOPIC="projects/${PROJECT_ID}/topics/harness-events"
GITHUB="https://github.com/weimeilin79/DinoQuest"
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
CIAGENT_URL=https://ci-agent-${PROJECT_NUMBER}.us-central1.run.app


gcloud run deploy remediation-agent \
  --image=$IMAGE \
  --region=us-central1 \
  --service-account=$SA \
  --memory=2Gi \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=True" \
  --set-env-vars="GITHUB_REPO_URL=${GITHUB}" \
  --set-env-vars="SLACK_WEBHOOK_SECRET=projects/${PROJECT_ID}/secrets/slack-webhook/versions/latest" \
  --set-env-vars="HARNESS_EVENTS_TOPIC=${TOPIC}"\
  --set-env-vars="CIAGENT_URL=${CIAGENT_URL}" \
  --set-secrets="GITHUB_TOKEN=github-token:latest" \
  --no-allow-unauthenticated \
  --timeout=300
```

> Omit `--set-env-vars="SLACK_WEBHOOK_SECRET=..."` if you don't need Slack notifications. You can also pass the URL directly with `--set-env-vars="SLACK_WEBHOOK_URL=https://hooks.slack.com/..."` for a simpler setup.

> No API key needed — the service authenticates to Vertex AI via the attached service account's Application Default Credentials.

### 3. Create a Pub/Sub topic for Cloud Run errors

```bash
gcloud pubsub topics create cloud-run-errors
```

### 4. Create a Cloud Logging sink

Route error logs from Cloud Run to the Pub/Sub topic:

```bash
FILTER='resource.type="cloud_run_revision" resource.labels.service_name="dinoquest" severity=ERROR NOT logName=~"cloudaudit" NOT httpRequest.requestUrl=~"/_ah/health"'

gcloud logging sinks create cloud-run-errors-sink \
  pubsub.googleapis.com/projects/${PROJECT_ID}/topics/cloud-run-errors \
  --log-filter="$FILTER"
```

> Scope the filter to the specific service name you want to monitor. Adjust `resource.labels.service_name` as needed.
> The `NOT logName=~"cloudaudit"` clause is important — without it the agent will process its own GCP API audit logs and chase its tail.

Then grant the sink's writer identity permission to publish to the topic:

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

Edit `skills/remediation/SKILL.md` to change the agent's behavior — add new remediation strategies, adjust guardrails, or extend the output format — without touching any Python code. Rebuild and redeploy the image to pick up changes.

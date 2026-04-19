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
├── tools.py             # Cloud Run v2 API tools
├── skills/
│   └── remediation/
│       └── SKILL.md     # Agent playbook — edit this to change behavior
├── requirements.txt
└── Dockerfile
```

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
FILTER='resource.type="cloud_run_revision" resource.labels.service_name="dinoquest2" severity=ERROR NOT httpRequest.requestUrl="/_ah/health"'

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
FILTER='resource.type="cloud_run_revision" resource.labels.service_name="dinoquest2" severity=ERROR NOT httpRequest.requestUrl="/_ah/health"'

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

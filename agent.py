import json
import logging
import os
import subprocess
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset

from tools import (
    get_service,
    list_revisions,
    list_services,
    rollback_traffic,
    update_service_env_vars,
    update_service_resources,
)

_SKILL_DIR = Path(__file__).parent / "skills" / "remediation"
_SCRIPTS_DIR = _SKILL_DIR / "scripts"

log = logging.getLogger(__name__)


def _resolve_github_token() -> None:
    """Ensure GITHUB_TOKEN is set in the process environment.

    Resolution order:
    1. GITHUB_TOKEN env var already set (local dev, CI, or Cloud Run --set-secrets mount).
    2. Fetch from Secret Manager using the resource name in GITHUB_TOKEN_SECRET
       (e.g. 'projects/my-project/secrets/github-token/versions/latest').
       The Cloud Run service account must have roles/secretmanager.secretAccessor.
    """
    if os.environ.get("GITHUB_TOKEN"):
        return

    secret_name = os.environ.get("GITHUB_TOKEN_SECRET")
    if not secret_name:
        log.warning("GITHUB_TOKEN and GITHUB_TOKEN_SECRET are both unset — code-fix track will fail")
        return

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_name)
    os.environ["GITHUB_TOKEN"] = response.payload.data.decode()
    log.info("GITHUB_TOKEN loaded from Secret Manager")


def _run_script(script: str, args: list[str], stdin: str | None = None) -> str:
    r = subprocess.run(
        ["bash", str(_SCRIPTS_DIR / script), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )
    return r.stdout.strip() if r.returncode == 0 else r.stderr.strip()


def build_agent() -> LlmAgent:
    """Build the remediation LlmAgent with Cloud Run tools bound to the current project/region."""
    _resolve_github_token()
    project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
    region = os.environ.get("CLOUD_RUN_REGION", "us-central1")

    # Closures inject project/region so the agent only needs service names as args.
    def _list_services() -> str:
        """List all Cloud Run services in the current project and region."""
        return list_services(project_id, region)

    def _get_service(service_name: str) -> str:
        """Get detailed configuration and health of a specific Cloud Run service."""
        return get_service(project_id, region, service_name)

    def _list_revisions(service_name: str) -> str:
        """List the 10 most recent revisions of a Cloud Run service, newest first."""
        return list_revisions(project_id, region, service_name)

    def _rollback_traffic(service_name: str, revision_name: str, percent: int = 100) -> str:
        """Route traffic to a specific revision. Use for rollbacks."""
        return rollback_traffic(project_id, region, service_name, revision_name, percent)

    def _update_service_env_vars(service_name: str, env_vars: dict) -> str:
        """Add or update environment variables on a Cloud Run service."""
        return update_service_env_vars(project_id, region, service_name, env_vars)

    def _update_service_resources(service_name: str, memory: str | None = None, cpu: str | None = None) -> str:
        """Update memory and/or CPU limits on a Cloud Run service. memory e.g. '1Gi', '2Gi'. cpu e.g. '1', '2'."""
        return update_service_resources(project_id, region, service_name, memory, cpu)

    github_repo_url = os.environ.get("GITHUB_REPO_URL", "")

    def _clone_repo() -> str:
        """Clone the application repository. Returns local_path used by subsequent code-fix tools."""
        if not github_repo_url:
            return json.dumps({"status": "error", "message": "GITHUB_REPO_URL is not configured"})
        return _run_script("clone_repo.sh", [github_repo_url])

    def _read_repo_file(local_path: str, relative_file_path: str) -> str:
        """Read a source file from the cloned repo. Always call before applying a fix."""
        return _run_script("read_file.sh", [local_path, relative_file_path])

    def _apply_code_fix(local_path: str, relative_file_path: str, new_content: str) -> str:
        """Overwrite a file in the cloned repo with corrected content (piped via stdin)."""
        return _run_script("apply_fix.sh", [local_path, relative_file_path], stdin=new_content)

    def _commit_to_incident_branch(local_path: str, incident_datetime: str, commit_message: str) -> str:
        """Create branch incident_YYMMDDHH, stage all changes, commit, and push to origin.

        incident_datetime: ISO 8601 timestamp from the error log, e.g. '2026-04-20T14:30:00Z'.
        """
        return _run_script("commit_branch.sh", [local_path, incident_datetime, commit_message])

    def _open_pull_request(local_path: str, title: str, body: str) -> str:
        """Open a GitHub pull request from the current incident branch. Call after _commit_to_incident_branch."""
        return _run_script("open_pr.sh", [local_path, title, body])

    def _rollback_fix(local_path: str, branch_name: str) -> str:
        """Close the PR and delete the incident branch to roll back a code fix. Safe to call if already closed."""
        return _run_script("rollback_fix.sh", [local_path, branch_name])

    skill = load_skill_from_dir(_SKILL_DIR)
    remediation_toolset = skill_toolset.SkillToolset(skills=[skill])

    return LlmAgent(
        name="cloud_run_remediation",
        model="gemini-2.5-flash",
        instruction=(
            "You are an SRE remediation agent for Dino Quest. "
            "You are invoked automatically when an error log is detected. "
            "Your job is to diagnose the error and take corrective action. "
            "Use the remediation skill to guide your investigation and decision-making. "
            "Always inspect the current service state before acting, and never take destructive "
            "action without evidence from the error and the service conditions."
        ),
        tools=[
            remediation_toolset,
            _list_services,
            _get_service,
            _list_revisions,
            _rollback_traffic,
            _update_service_env_vars,
            _update_service_resources,
            _clone_repo,
            _read_repo_file,
            _apply_code_fix,
            _commit_to_incident_branch,
            _open_pull_request,
            _rollback_fix,
        ],
    )

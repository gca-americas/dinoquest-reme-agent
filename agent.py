import json
import logging
import os
import subprocess
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from google.adk.tools.agent_tool import AgentTool

from utils import emit_event, resolve_secret

import threading
_cid = threading.local()

def set_correlation_id(cid: str) -> None:
    _cid.value = cid

def _cid_get() -> str:
    return getattr(_cid, "value", "")

from tools import (
    get_service as _get_service_impl,
    list_revisions as _list_revisions_impl,
    list_services as _list_services_impl,
    rollback_traffic as _rollback_traffic_impl,
    update_service_env_vars as _update_service_env_vars_impl,
    update_service_resources as _update_service_resources_impl,
)

_SKILL_DIR = Path(__file__).parent / "skills" / "remediation"
_SCRIPTS_DIR = _SKILL_DIR / "scripts"

log = logging.getLogger(__name__)


def _resolve_github_token() -> None:
    token = resolve_secret("GITHUB_TOKEN", "GITHUB_TOKEN_SECRET")
    if token:
        os.environ["GITHUB_TOKEN"] = token
    else:
        log.warning("GITHUB_TOKEN and GITHUB_TOKEN_SECRET are both unset — code-fix track will fail")


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

    def list_services() -> str:
        """List all Cloud Run services in the current project and region."""
        return _list_services_impl(project_id, region)

    def get_service(service_name: str) -> str:
        """Get detailed configuration and health of a specific Cloud Run service."""
        return _get_service_impl(project_id, region, service_name)

    def list_revisions(service_name: str) -> str:
        """List the 10 most recent revisions of a Cloud Run service, newest first."""
        return _list_revisions_impl(project_id, region, service_name)

    def rollback_traffic(service_name: str, revision_name: str, percent: int = 100) -> str:
        """Route traffic to a specific revision. Use for rollbacks."""
        emit_event("DinoAgent", "thinking",
                   {"summary": f"Rolling back Cloud Run {service_name} → {revision_name} ({percent}% traffic)"},
                   _cid_get())
        return _rollback_traffic_impl(project_id, region, service_name, revision_name, percent)

    def update_service_env_vars(service_name: str, env_vars: dict) -> str:
        """Add or update environment variables on a Cloud Run service."""
        keys = ", ".join(env_vars.keys())
        emit_event("DinoAgent", "thinking",
                   {"summary": f"Updating Cloud Run {service_name} env vars: {keys}"},
                   _cid_get())
        return _update_service_env_vars_impl(project_id, region, service_name, env_vars)

    def update_service_resources(service_name: str, memory: str | None = None, cpu: str | None = None) -> str:
        """Update memory and/or CPU limits on a Cloud Run service. memory e.g. '1Gi', '2Gi'. cpu e.g. '1', '2'.
        At least one of memory or cpu must be provided."""
        if not memory and not cpu:
            return json.dumps({"error": "at least one of memory or cpu must be provided"})
        parts = [f"memory → {memory}" if memory else None, f"cpu → {cpu}" if cpu else None]
        desc  = ", ".join(p for p in parts if p)
        emit_event("DinoAgent", "thinking",
                   {"summary": f"Patching Cloud Run service {service_name}: {desc}"},
                   _cid_get())
        return _update_service_resources_impl(project_id, region, service_name, memory, cpu)

    github_repo_url = os.environ.get("GITHUB_REPO_URL", "")

    def clone_repo() -> str:
        """Clone the application repository. Returns local_path used by subsequent code-fix tools."""
        if not github_repo_url:
            return json.dumps({"status": "error", "message": "GITHUB_REPO_URL is not configured"})
        emit_event("DinoAgent", "thinking",
                   {"summary": "Cloning repository for root-cause analysis"},
                   _cid_get())
        return _run_script("clone_repo.sh", [github_repo_url])

    def read_repo_file(local_path: str, relative_file_path: str) -> str:
        """Read a source file from the cloned repo. Always call before applying a fix."""
        return _run_script("read_file.sh", [local_path, relative_file_path])

    def apply_code_fix(local_path: str, relative_file_path: str, new_content: str) -> str:
        """Overwrite a file in the cloned repo with corrected content (piped via stdin)."""
        emit_event("DinoAgent", "pipeline_step",
                   {"step": f"fix: editing {relative_file_path}"},
                   _cid_get())
        return _run_script("apply_fix.sh", [local_path, relative_file_path], stdin=new_content)

    def commit_to_incident_branch(local_path: str, incident_datetime: str, commit_message: str) -> str:
        """Create branch incident_YYMMDDHH, stage all changes, commit, and push to origin.

        incident_datetime: ISO 8601 timestamp from the error log, e.g. '2026-04-20T14:30:00Z'.
        """
        emit_event("DinoAgent", "thinking",
                   {"summary": f"Pushing branch: committing fix — {commit_message}"},
                   _cid_get())
        return _run_script("commit_branch.sh", [local_path, incident_datetime, commit_message])

    def open_pull_request(local_path: str, title: str, body: str) -> str:
        """Open a GitHub pull request from the current incident branch. Call after commit_to_incident_branch."""
        emit_event("DinoAgent", "thinking",
                   {"summary": f"Opening pull request: {title}"},
                   _cid_get())
        return _run_script("open_pr.sh", [local_path, title, body])

    def rollback_fix(local_path: str, branch_name: str) -> str:
        """Close the PR and delete the incident branch to roll back a code fix. Safe to call if already closed."""
        emit_event("DinoAgent", "thinking",
                   {"summary": f"Rolling back code fix: closing PR and deleting branch {branch_name}"},
                   _cid_get())
        return _run_script("rollback_fix.sh", [local_path, branch_name])

    ciagent_url = os.environ.get("CIAGENT_URL", "")

    ci_agent_tool = None
    if ciagent_url:
        _ci_remote = RemoteA2aAgent(
            name="ci_agent",
            agent_card=f"{ciagent_url}/.well-known/agent-card.json",
            description=(
                "CIAgent — autonomous CI pipeline for DinoQuest. "
                "Teach it a new skill by sending it the bug description, detection pattern, "
                "and recommended action so it can register the skill and add a test case."
            ),
        )
        ci_agent_tool = AgentTool(agent=_ci_remote)

    def announce_a2a_to_ci(message_preview: str) -> str:
        """Emit a2a_call_sent so the theater shows the animated dot flying to CIAgent.
        Always call this immediately before calling ci_agent."""
        emit_event("DinoAgent", "a2a_call_sent", {
            "target_agent": "CIAgent",
            "method": "build_and_deploy",
            "args_preview": message_preview[:100],
        }, _cid_get())
        return json.dumps({"status": "announced"})

    skill = load_skill_from_dir(_SKILL_DIR)
    remediation_toolset = skill_toolset.SkillToolset(skills=[skill])

    ci_instruction = (
        "\n\nAfter you successfully open a pull request for a code fix, hand off to CIAgent "
        "to build, test, and deploy the fix:\n"
        "1. Call announce_a2a_to_ci with a one-line preview, e.g. 'Building and deploying incident fix branch'.\n"
        "2. Call ci_agent with EXACTLY this message format:\n"
        "   'Build and deploy branch <INCIDENT_BRANCH_NAME>'\n"
        "   where INCIDENT_BRANCH_NAME is the branch name returned by commit_to_incident_branch "
        "(e.g. 'incident_26050202'). Do not modify or summarize — pass the exact branch name."
    ) if ci_agent_tool else ""

    extra_tools = [announce_a2a_to_ci, ci_agent_tool] if ci_agent_tool else [announce_a2a_to_ci]

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
            + ci_instruction
        ),
        tools=[
            remediation_toolset,
            list_services,
            get_service,
            list_revisions,
            rollback_traffic,
            update_service_env_vars,
            update_service_resources,
            clone_repo,
            read_repo_file,
            apply_code_fix,
            commit_to_incident_branch,
            open_pull_request,
            rollback_fix,
            *extra_tools,
        ],
    )

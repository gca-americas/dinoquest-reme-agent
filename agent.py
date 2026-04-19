import os
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


def build_agent() -> LlmAgent:
    """Build the remediation LlmAgent with Cloud Run tools bound to the current project/region."""
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
        ],
    )

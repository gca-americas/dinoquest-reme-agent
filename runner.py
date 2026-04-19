from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from agent import build_agent

APP_NAME = "cloud_run_remediation"


def build_runner() -> Runner:
    """Build the ADK Runner wired to the remediation agent."""
    return Runner(
        agent=build_agent(),
        app_name=APP_NAME,
        session_service=InMemorySessionService(),
    )

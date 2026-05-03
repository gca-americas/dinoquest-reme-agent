import json
import time
from typing import Callable

from google.api_core import exceptions as _api_exc
from google.cloud import run_v2


def _update_with_retry(
    name: str,
    mutate: Callable[[run_v2.Service], None],
    max_retries: int = 4,
) -> run_v2.Service:
    """Fetch → mutate → update_service, retrying on optimistic-lock conflicts (ABORTED).

    The Cloud Run API uses etag-based optimistic locking. When two concurrent
    callers both fetch the same version and one updates first, the other gets
    ABORTED. We re-fetch and re-apply the mutation on each retry.
    """
    client = run_v2.ServicesClient()
    for attempt in range(max_retries + 1):
        svc = client.get_service(name=name)
        mutate(svc)
        try:
            # timeout=300: Cloud Run LROs can queue behind concurrent updates;
            # the default gRPC deadline (~60s) is too short in those cases.
            return client.update_service(service=svc).result(timeout=300)
        except _api_exc.Aborted:
            if attempt == max_retries:
                raise
            time.sleep(1.5 ** attempt)  # 1s, 1.5s, 2.25s, 3.4s
    raise RuntimeError("unreachable")


def list_services(project_id: str, region: str) -> str:
    """List all Cloud Run services in the current project and region."""
    client = run_v2.ServicesClient()
    parent = f"projects/{project_id}/locations/{region}"
    services = []
    for svc in client.list_services(parent=parent):
        services.append({
            "name": svc.name.split("/")[-1],
            "uri": svc.uri,
            "latest_ready_revision": svc.latest_ready_revision.split("/")[-1] if svc.latest_ready_revision else None,
            "conditions": [
                {"type": c.type_, "state": str(c.state), "message": c.message}
                for c in svc.conditions
            ],
        })
    return json.dumps({"services": services})


def get_service(project_id: str, region: str, service_name: str) -> str:
    """Get detailed configuration and health of a specific Cloud Run service."""
    client = run_v2.ServicesClient()
    name = f"projects/{project_id}/locations/{region}/services/{service_name}"
    svc = client.get_service(name=name)
    containers = [
        {
            "image": c.image,
            "env_vars": {e.name: e.value for e in c.env if e.value},
            "resource_limits": dict(c.resources.limits),
        }
        for c in svc.template.containers
    ]
    return json.dumps({
        "name": svc.name.split("/")[-1],
        "uri": svc.uri,
        "containers": containers,
        "scaling": {
            "min_instance_count": svc.template.scaling.min_instance_count,
            "max_instance_count": svc.template.scaling.max_instance_count,
        },
        "conditions": [
            {"type": c.type_, "state": str(c.state), "message": c.message}
            for c in svc.conditions
        ],
        "latest_ready_revision": svc.latest_ready_revision.split("/")[-1] if svc.latest_ready_revision else None,
        "latest_created_revision": svc.latest_created_revision.split("/")[-1] if svc.latest_created_revision else None,
        "traffic": [
            {"revision": t.revision.split("/")[-1] if t.revision else "latest", "percent": t.percent}
            for t in svc.traffic_statuses
        ],
    })


def list_revisions(project_id: str, region: str, service_name: str) -> str:
    """List the 10 most recent revisions of a Cloud Run service, newest first."""
    client = run_v2.RevisionsClient()
    parent = f"projects/{project_id}/locations/{region}/services/{service_name}"
    revisions = []
    for rev in client.list_revisions(parent=parent):
        revisions.append({
            "name": rev.name.split("/")[-1],
            "create_time": str(rev.create_time),
            "conditions": [
                {"type": c.type_, "state": str(c.state), "message": c.message}
                for c in rev.conditions
            ],
        })
        if len(revisions) >= 10:
            break
    return json.dumps({"revisions": revisions})


def rollback_traffic(
    project_id: str,
    region: str,
    service_name: str,
    revision_name: str,
    percent: int = 100,
) -> str:
    """Route traffic to a specific revision. Use for rollbacks. percent defaults to 100."""
    name = f"projects/{project_id}/locations/{region}/services/{service_name}"

    def _mutate(svc):
        svc.traffic = [
            run_v2.TrafficTarget(
                type_=run_v2.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
                revision=revision_name,
                percent=percent,
            )
        ]

    _update_with_retry(name, _mutate)
    return json.dumps({"status": "traffic_updated", "revision": revision_name, "percent": percent})


def update_service_resources(
    project_id: str,
    region: str,
    service_name: str,
    memory: str | None = None,
    cpu: str | None = None,
) -> str:
    """Update memory and/or CPU limits on a Cloud Run service. Triggers a new revision.

    memory: e.g. '512Mi', '1Gi', '2Gi', '4Gi'
    cpu:    e.g. '1', '2', '4'
    """
    name = f"projects/{project_id}/locations/{region}/services/{service_name}"

    def _mutate(svc):
        current_limits = dict(svc.template.containers[0].resources.limits)
        if memory:
            current_limits["memory"] = memory
        if cpu:
            current_limits["cpu"] = cpu
        svc.template.containers[0].resources.limits = current_limits

    result = _update_with_retry(name, _mutate)
    new_limits = dict(result.template.containers[0].resources.limits)
    return json.dumps({
        "status": "updated",
        "service": service_name,
        "new_limits": new_limits,
        "new_revision": result.latest_created_revision.split("/")[-1] if result.latest_created_revision else None,
    })


def update_service_env_vars(
    project_id: str,
    region: str,
    service_name: str,
    env_vars: dict,
) -> str:
    """Add or update environment variables on a Cloud Run service. Triggers a new revision."""
    name = f"projects/{project_id}/locations/{region}/services/{service_name}"

    def _mutate(svc):
        existing = {e.name: e.value for e in svc.template.containers[0].env if e.value}
        existing.update(env_vars)
        svc.template.containers[0].env = [
            run_v2.EnvVar(name=k, value=v) for k, v in existing.items()
        ]

    result = _update_with_retry(name, _mutate)
    return json.dumps({
        "status": "updated",
        "service": service_name,
        "new_revision": result.latest_created_revision.split("/")[-1] if result.latest_created_revision else None,
    })

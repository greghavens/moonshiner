"""Stdlib-only namespace inventory and Supervisor backup client."""

from __future__ import annotations

from typing import Any


GET_NAMESPACE_OPERATION = "Vcenter.Namespaces.Instances_getV2"
LIST_CLUSTERS_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)
CREATE_BACKUP_OPERATION = (
    "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create"
)
GET_TASK_OPERATION = "Cis.Tasks_get"


class ApiError(RuntimeError):
    """An HTTP or transport failure."""


class ProtocolError(RuntimeError):
    """A malformed successful response or inconsistent inventory."""


class TaskFailedError(RuntimeError):
    """A backup task that reached FAILED."""


class PollTimeoutError(RuntimeError):
    """A backup task that did not terminate within the poll limit."""


class NamespaceBackupClient:
    """Back up a namespace's Supervisor around a stable VKS inventory."""

    def __init__(
        self,
        vcenter_url: str,
        kubernetes_url: str,
        vcenter_session_id: str,
        kubernetes_token: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.0,
        max_polls: int = 8,
    ) -> None:
        raise NotImplementedError("Implement the contract-backed client.")

    def backup_namespace(
        self,
        namespace: str,
        *,
        comment: str | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError(
            "Inventory, submit, poll, and verify the namespace backup."
        )

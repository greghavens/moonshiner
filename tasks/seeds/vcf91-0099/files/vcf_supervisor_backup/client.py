"""Stdlib-only client for the focused vCenter Supervisor backup contract."""

from dataclasses import dataclass
from typing import Any


CREATE_BACKUP_OPERATION = (
    "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create"
)
GET_TASK_OPERATION = "Cis.Tasks_get"


class VcenterError(RuntimeError):
    """An HTTP or transport failure."""


class ProtocolError(RuntimeError):
    """A malformed successful response."""


class TaskFailedError(RuntimeError):
    """A task that reached FAILED."""


class PollTimeoutError(RuntimeError):
    """A task that did not terminate within the configured poll limit."""


@dataclass(frozen=True)
class BackupResult:
    task_id: str
    status: str
    result: Any
    poll_count: int


class SupervisorBackupClient:
    """Submit a Supervisor backup and poll its CIS task."""

    def __init__(
        self,
        base_url: str,
        session_token: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.0,
        max_polls: int = 8,
    ) -> None:
        raise NotImplementedError("Implement the contract-backed client.")

    def create_backup(
        self,
        supervisor: str,
        *,
        comment: str | None = None,
        ignore_health_check_failure: bool | None = None,
    ) -> BackupResult:
        raise NotImplementedError("Submit and poll the Supervisor backup task.")

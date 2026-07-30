"""Stdlib-only client for the focused vCenter clone batch contract."""

from dataclasses import dataclass
from typing import Any


CLONE_OPERATION = "Vcenter.VM_clone$Task"
TASK_LIST_OPERATION = "Cis.Tasks_list"


class VcenterError(RuntimeError):
    """An HTTP or transport failure."""


class ProtocolError(RuntimeError):
    """A malformed successful response."""


class TaskFailedError(RuntimeError):
    """A task that reached FAILED."""


class PollTimeoutError(RuntimeError):
    """A batch that did not terminate within the configured poll limit."""


@dataclass(frozen=True)
class CloneRequest:
    source_vm: str
    name: str


@dataclass(frozen=True)
class CloneResult:
    task_id: str
    source_vm: str
    name: str
    status: str
    result: Any
    poll_count: int


class CloneBatchClient:
    """Submit vCenter clone tasks and poll the batch to a terminal state."""

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

    def clone_batch(self, requests: Any) -> list[CloneResult]:
        raise NotImplementedError("Submit, poll, associate, and sort the batch.")

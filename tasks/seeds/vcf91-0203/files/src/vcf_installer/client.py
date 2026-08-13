"""Client implementation target for the pinned VCF Installer 9.1 contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class VcfInstallerError(Exception):
    """Base class for client errors."""


class ApiError(VcfInstallerError):
    """An HTTP or response-decoding error."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ProtocolError(VcfInstallerError):
    """A successful response did not follow the pinned contract."""


class PollTimeoutError(VcfInstallerError):
    """The task did not reach a terminal state before the timeout."""


class TaskFailedError(VcfInstallerError):
    """The asynchronous operation reached a non-success terminal state."""

    def __init__(self, task: dict[str, Any]):
        self.task = task
        super().__init__(f"task reached terminal status {task.get('status')!r}")


@dataclass(frozen=True)
class BundleDownloadSpec:
    scheduled_timestamp: str | None = None
    download_now: bool | None = None
    cancel_now: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return a BundleUpdateSpec JSON value, omitting unset options."""
        raise NotImplementedError


class VcfInstallerClient:
    """Client for the two operations in docs/contract.json."""

    def __init__(self, base_url: str, *, request_timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds

    def start_bundle_download(
        self, bundle_id: str, spec: BundleDownloadSpec
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_task(self, task_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def download_bundle_and_wait(
        self,
        bundle_id: str,
        spec: BundleDownloadSpec,
        *,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float = 900.0,
    ) -> dict[str, Any]:
        raise NotImplementedError

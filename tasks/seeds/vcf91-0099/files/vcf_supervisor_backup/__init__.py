"""Focused VMware vCenter Supervisor backup client."""

from .client import (
    BackupResult,
    PollTimeoutError,
    ProtocolError,
    SupervisorBackupClient,
    TaskFailedError,
    VcenterError,
)

__all__ = [
    "BackupResult",
    "PollTimeoutError",
    "ProtocolError",
    "SupervisorBackupClient",
    "TaskFailedError",
    "VcenterError",
]

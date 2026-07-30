"""Public interface for the namespace-aware Supervisor backup client."""

from .client import (
    ApiError,
    NamespaceBackupClient,
    PollTimeoutError,
    ProtocolError,
    TaskFailedError,
)

__all__ = [
    "ApiError",
    "NamespaceBackupClient",
    "PollTimeoutError",
    "ProtocolError",
    "TaskFailedError",
]

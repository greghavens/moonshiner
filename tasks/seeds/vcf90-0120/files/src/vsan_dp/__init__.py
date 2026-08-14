"""Small, standard-library-only vSAN Data Protection client."""

from .client import (
    ApiError,
    RetentionPeriod,
    SnapshotClient,
    TaskFailedError,
    VsanDpError,
)

__all__ = [
    "ApiError",
    "RetentionPeriod",
    "SnapshotClient",
    "TaskFailedError",
    "VsanDpError",
]

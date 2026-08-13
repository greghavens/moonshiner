"""Stdlib-only client for the VMware Cloud Foundation 9.0 SDDC Manager
bundle-download workflow."""

from .client import (
    NON_TERMINAL_STATUSES,
    TERMINAL_FAILURE_STATUSES,
    TERMINAL_SUCCESS_STATUSES,
    BundleDownloadClient,
    DownloadResult,
    normalize_status,
)
from .errors import (
    ApiError,
    AuthenticationError,
    SddcManagerError,
    TaskFailedError,
    TaskTimeoutError,
)

__all__ = [
    "ApiError",
    "AuthenticationError",
    "BundleDownloadClient",
    "DownloadResult",
    "NON_TERMINAL_STATUSES",
    "SddcManagerError",
    "TERMINAL_FAILURE_STATUSES",
    "TERMINAL_SUCCESS_STATUSES",
    "TaskFailedError",
    "TaskTimeoutError",
    "normalize_status",
]

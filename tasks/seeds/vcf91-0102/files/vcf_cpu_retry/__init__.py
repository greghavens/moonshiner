"""Retry-safe stdlib client for one pinned vCenter operation."""

from .client import (
    CpuUpdateClient,
    CpuUpdateResult,
    ProtocolError,
    RetryExhaustedError,
    VcenterError,
)

__all__ = [
    "CpuUpdateClient",
    "CpuUpdateResult",
    "VcenterError",
    "ProtocolError",
    "RetryExhaustedError",
]

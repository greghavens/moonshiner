"""Dependency-free client for the VCF 9.1 vSAN Data Protection snapshot appliance.

The wire contract this package implements is recorded in docs/contract.json,
which is derived from the vSAN Data Protection OpenAPI specification published in
the vmware/vcf-api-specs repository. See docs/official_sources.json for the exact
specification revision.
"""

from .client import (
    ApiError,
    RetentionPeriod,
    SnapshotClient,
    TaskFailedError,
    TaskTimeoutError,
    VsanDpError,
)

__all__ = [
    "ApiError",
    "RetentionPeriod",
    "SnapshotClient",
    "TaskFailedError",
    "TaskTimeoutError",
    "VsanDpError",
]

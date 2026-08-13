"""Dependency-free client for the VCF Operations 9.1 alert sweep.

Wire behaviour is fixed by docs/contract.json, which is derived from
specifications/vcf-operations/vcf-operations-openapi.json in vmware/vcf-api-specs.
"""

from .client import (
    AuthenticationFailed,
    TokenExpired,
    VcfOperationsClient,
    VcfOperationsError,
    build_acquire_body,
    build_alert_query,
)
from .collect import SweepResult, sweep_alerts

__all__ = [
    "AuthenticationFailed",
    "SweepResult",
    "TokenExpired",
    "VcfOperationsClient",
    "VcfOperationsError",
    "build_acquire_body",
    "build_alert_query",
    "sweep_alerts",
]

"""Stdlib-only evidence collector for VCF 9.1 VKS workloads."""

from .client import DiagnosticError, KubernetesClient, VCenterClient
from .diagnosis import diagnose_workload

__all__ = [
    "DiagnosticError",
    "KubernetesClient",
    "VCenterClient",
    "diagnose_workload",
]

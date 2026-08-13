"""Stdlib-only client for the VCF Automation provisioning service (VCF 9.1)."""

from .errors import (
    ApiError,
    ProvisioningFailed,
    ProvisioningTimeout,
    VcfAutomationError,
)
from .models import (
    Constraint,
    DiskSpec,
    MachineSpec,
    NetworkInterfaceSpec,
    ProvisionResult,
    Tag,
)
from .client import VcfAutomationClient

__all__ = [
    "VcfAutomationClient",
    "MachineSpec",
    "NetworkInterfaceSpec",
    "DiskSpec",
    "Constraint",
    "Tag",
    "ProvisionResult",
    "VcfAutomationError",
    "ApiError",
    "ProvisioningFailed",
    "ProvisioningTimeout",
]

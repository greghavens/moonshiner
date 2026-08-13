"""Stdlib-only client for the vCenter migration-upgrade operations of the
vSphere Automation API shipped with VMware Cloud Foundation 9.0.

The REST contract lives in ``docs/contract.json`` and is derived from
``specifications/vsphere/openapi/automation/vcenter.yaml`` at tag ``9.0.0.0``
of the ``vmware/vcf-api-specs`` repository (Apache-2.0).
"""

from __future__ import annotations

from .client import MigrationUpgradeClient
from .contract import Contract, Operation, load_contract
from .errors import (
    ApiError,
    InvalidApplySpec,
    UpgradeNotConfigured,
    UpgradePollTimeout,
    VcfLcmError,
)
from .upgrade import TERMINAL_STATUSES, MigrationUpgradeDriver, UpgradeOutcome

__all__ = [
    "ApiError",
    "Contract",
    "InvalidApplySpec",
    "MigrationUpgradeClient",
    "MigrationUpgradeDriver",
    "Operation",
    "TERMINAL_STATUSES",
    "UpgradeNotConfigured",
    "UpgradeOutcome",
    "UpgradePollTimeout",
    "VcfLcmError",
    "load_contract",
]

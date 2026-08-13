"""Stdlib-only VCF 9.1 vSAN Data Protection snapshot inventory collector."""

from .client import SnapserviceError, collect_vm_snapshot_inventory

__all__ = ["SnapserviceError", "collect_vm_snapshot_inventory"]

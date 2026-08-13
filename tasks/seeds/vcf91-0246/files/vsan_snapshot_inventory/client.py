"""VCF 9.1 vSAN Data Protection virtual machine snapshot inventory."""

from __future__ import annotations

from os import PathLike
from typing import Any


class SnapserviceError(RuntimeError):
    """Raised when the Snapshot Appliance rejects or breaks a request."""


def collect_vm_snapshot_inventory(
    service_root: str,
    username: str,
    password: str,
    cluster: str,
    report_path: str | PathLike[str],
    *,
    page_size: int = 25,
    created_after: str | None = None,
    created_before: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Collect every snapshot page and return the stable inventory report."""

    raise NotImplementedError("implement the contract-pinned inventory collection")

"""Diagnose the most recent failed SDDC Manager task and collect its evidence."""

from __future__ import annotations

# Resource type -> the Logs flag that collects that component's logs.
LOG_FLAG_BY_COMPONENT_TYPE = {
    "ESXI": "esxLogs",
    "NSXT_MANAGER": "nsxLogs",
    "VCENTER": "vcLogs",
    "SDDC_MANAGER": "sddcManagerLogs",
}


def diagnose_failure(base_url: str, username: str, password: str) -> dict:
    """Diagnose the most recent failed task and return the diagnosis report.

    See README.md for the required report keys and the derivation rules.
    """
    raise NotImplementedError

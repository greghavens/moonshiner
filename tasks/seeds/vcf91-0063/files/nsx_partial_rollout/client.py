"""VCF 9.1 NSX Policy partial-rollout workflow."""

from __future__ import annotations

from os import PathLike
from typing import Any, Mapping


def apply_firewall_change(
    base_url: str,
    username: str,
    password: str,
    domain_id: str,
    group_id: str,
    security_policy_id: str,
    plan: Mapping[str, Any],
    report_path: str | PathLike[str],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Apply the two-step change and return its deterministic outcome report."""

    raise NotImplementedError("implement the contract-pinned workflow")

"""Precheck-gated host commissioning against the SDDC Manager REST API.

Fill in the implementation described in the task. The contract for the three
operations in scope is projected in ``docs/contract.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SddcManagerError(Exception):
    """Base error for every SDDC Manager failure this package raises."""


class PrecheckFailedError(SddcManagerError):
    """The host commission precheck reached a terminal state that is not a pass."""


class PrecheckTimeoutError(SddcManagerError):
    """The host commission precheck never reached a terminal state."""


@dataclass(frozen=True)
class HostCommissionSpec:
    """One host to commission, mirroring the HostCommissionSpec schema."""

    fqdn: str

    def to_wire(self) -> dict[str, str]:
        raise NotImplementedError("HostCommissionSpec.to_wire is not implemented yet")


@dataclass(frozen=True)
class CommissionOutcome:
    """What a gated commissioning attempt observed and did."""

    validation_id: str


class SddcManagerClient:
    """Thin client over the three contracted host commissioning operations."""

    def __init__(self, base_url: str, token: str, **options: Any) -> None:
        raise NotImplementedError("SddcManagerClient is not implemented yet")

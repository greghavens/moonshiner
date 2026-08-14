"""Focused VCF Automation client implementation.

Only Python's standard library may be used.  The public declarations in this
file are part of the exercise contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CatalogRequest:
    """Values sent to ``Request Catalog Item Instances 1``."""

    deployment_name: str
    inputs: Mapping[str, Any]
    project_id: str
    bulk_request_count: int | None = None
    reason: str | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    """The terminal deployment returned by ``request_catalog_item``."""

    deployment_id: str
    deployment_name: str
    project_id: str | None
    status: str


class ApiError(RuntimeError):
    """A VCF Automation operation returned an unsuccessful HTTP status."""

    def __init__(self, operation: str, status: int) -> None:
        self.operation = operation
        self.status = status
        super().__init__(f"{operation} failed with HTTP {status}")


class ResponseContractError(RuntimeError):
    """A successful response did not match the focused contract."""


class DeploymentFailed(RuntimeError):
    """VCF Automation reported CREATE_FAILED for the requested deployment."""

    def __init__(self, deployment_id: str) -> None:
        self.deployment_id = deployment_id
        super().__init__(f"deployment {deployment_id!r} reached CREATE_FAILED")


class VcfAutomationClient:
    """Client for the three VCF Automation operations in ``docs/contract.json``."""

    def __init__(
        self,
        base_url: str,
        refresh_token: str,
        *,
        timeout: float = 5.0,
    ) -> None:
        raise NotImplementedError

    def request_catalog_item(
        self,
        catalog_item_id: str,
        request: CatalogRequest,
    ) -> DeploymentResult:
        raise NotImplementedError

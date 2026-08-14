"""VCF Automation 9.0 Project Service client.

The protected contract and verification fixture describe the required wire
behavior.  The implementation is intentionally incomplete.
"""

from __future__ import annotations

from typing import Any


class VCFAutomationError(RuntimeError):
    """Raised when VCF Automation does not return a usable success response."""


class PrecheckFailed(VCFAutomationError):
    """Raised when the project no longer has the expected current name."""


class ProjectClient:
    """Client for the two Project Service operations in docs/contract.json."""

    API_VERSION = "2019-01-15"

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def modify_project(
        self,
        project_id: str,
        *,
        expected_name: str,
        name: str,
        description: str | None = None,
        constraints: dict[str, Any] | None = None,
        properties: dict[str, Any] | None = None,
        operation_timeout: int | None = None,
        shared_resources: bool | None = None,
    ) -> dict[str, Any]:
        """Precheck the current name, then modify the project if it matches."""
        raise NotImplementedError("Project Service integration is not implemented")

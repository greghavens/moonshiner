"""VCF Automation deployment client."""

from __future__ import annotations

from typing import Any


class VCFAutomationError(RuntimeError):
    """An HTTP response from VCF Automation indicated failure."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        super().__init__(f"VCF Automation returned HTTP {status}")


class VCFAutomationClient:
    """Client for the deployment operation in ``docs/contract.json``."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def update_deployment(
        self,
        deployment_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        icon_id: str | None = None,
    ) -> dict[str, Any]:
        """Update deployment metadata and return the resulting deployment."""

        raise NotImplementedError("implement the contract-backed PATCH request")

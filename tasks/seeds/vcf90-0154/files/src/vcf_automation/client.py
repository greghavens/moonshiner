"""Standard-library client for the focused VCF Automation deployment contract."""

from __future__ import annotations

from typing import Any


class VCFAutomationClient:
    """Retrieve VCF Automation deployments."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        page_size: int = 20,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.page_size = page_size
        self.timeout = timeout

    def list_deployments(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return every deployment in stable name-and-id order."""
        raise NotImplementedError

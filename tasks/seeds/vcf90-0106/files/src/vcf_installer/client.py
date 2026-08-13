"""Standard-library-only client for the selected VCF Installer operations."""

from __future__ import annotations

from typing import Any, Mapping


class InstallerAPIError(RuntimeError):
    """An HTTP failure returned by the VCF Installer API."""

    def __init__(self, status: int, method: str, path: str, body: Any) -> None:
        self.status = status
        self.method = method
        self.path = path
        self.body = body
        super().__init__(f"VCF Installer request failed: {method} {path} ({status})")


class VCFInstallerClient:
    """Client for authentication and one VCF installation run."""

    TERMINAL_STATUSES = frozenset(
        {"COMPLETED_WITH_SUCCESS", "ROLLBACK_SUCCESS", "COMPLETED_WITH_FAILURE"}
    )

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.access_token: str | None = None
        self.refresh_token_id: str | None = None

    def create_token(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        id_token: str | None = None,
    ) -> dict[str, Any]:
        """Create and store an access/refresh token pair."""
        raise NotImplementedError

    def refresh_access_token(self) -> str:
        """Refresh and store the access token using the current refresh-token ID."""
        raise NotImplementedError

    def deploy_sddc(
        self,
        sddc_spec: Mapping[str, Any],
        *,
        skip_validations: bool | None = None,
    ) -> dict[str, Any]:
        """Submit an SDDC installation specification once."""
        raise NotImplementedError

    def get_sddc_task(self, task_id: str) -> dict[str, Any]:
        """Retrieve one SDDC installation task."""
        raise NotImplementedError

    def deploy_and_wait(
        self,
        sddc_spec: Mapping[str, Any],
        *,
        skip_validations: bool | None = None,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Submit an installation and poll its ID until it is terminal."""
        raise NotImplementedError

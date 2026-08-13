"""Standard-library client for the focused VCF Installer contract."""

from __future__ import annotations


class VcfInstallerError(RuntimeError):
    """Raised when a VCF Installer request or response is invalid."""


class VcfInstallerClient:
    """Client for the protected VCF Installer depot-update contract."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._access_token = access_token
        self._timeout = timeout

    def update_depot_settings(
        self,
        download_token: str,
        *,
        download_activation_code: str | None = None,
        max_retries: int = 1,
    ) -> dict[str, object]:
        """Replace the online depot settings with safe retry semantics."""
        raise NotImplementedError("implement idempotent updateDepotSettings retry")

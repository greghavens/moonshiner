"""Standard-library client for the focused VCF Installer depot contract."""

from __future__ import annotations

from typing import Any


class VCFInstallerClient:
    """Update the VCF Installer depot settings."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def set_depot_download_token(
        self,
        download_token: str,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Set the online depot download token."""
        raise NotImplementedError

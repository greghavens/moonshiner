"""VCF Installer task client implementation exercise."""

from __future__ import annotations


class VcfInstallerError(RuntimeError):
    """Raised when a VCF Installer request or response is invalid."""


class VcfInstallerClient:
    """Small focused client for the protected VCF Installer contract."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        refresh_token_id: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._access_token = access_token
        self._refresh_token_id = refresh_token_id
        self._timeout = timeout

    def list_tasks(self, *, page_size: int = 100) -> list[dict[str, object]]:
        """Return all Installer tasks from the focused contract."""
        raise NotImplementedError("implement the VCF Installer task workflow")

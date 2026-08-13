"""Standard-library client for the focused VCF Installer contract."""

from __future__ import annotations


class VcfInstallerError(RuntimeError):
    """Raised when a VCF Installer request or response is invalid."""


class VcfInstallerClient:
    """Client for the protected VCF Installer task-collection contract."""

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

    def list_tasks(self, *, page_size: int = 100) -> list[dict[str, object]]:
        """Return every Installer task in stable order."""
        raise NotImplementedError("implement paginated getTasks collection")

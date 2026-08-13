"""Retry-safe SSH credential rotation against the SDDC Manager 9.0 API."""

from __future__ import annotations


class SddcManagerError(RuntimeError):
    """Raised when an SDDC Manager request, response, or task state is unusable."""


class SddcManagerClient:
    """Stdlib-only SDDC Manager client for reconciled SSH password rotation."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._username = username
        self._password = password
        self._timeout = timeout
        raise NotImplementedError("SddcManagerClient is not implemented yet")

    def rotate_ssh_password(
        self,
        resource_type: str,
        resource_name: str,
        account_username: str,
        *,
        task_lookup_limit: int | None = 10,
    ) -> dict[str, object]:
        """Rotate one SSH account password without duplicating an existing rotation."""
        raise NotImplementedError("rotate_ssh_password is not implemented yet")

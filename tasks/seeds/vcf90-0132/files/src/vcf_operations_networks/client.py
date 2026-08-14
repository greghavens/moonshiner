"""VCF Operations for Networks API client.

The public surface is intentionally small.  See ``docs/contract.json`` for the
two OpenAPI operations used by this exercise.
"""

from __future__ import annotations

from typing import Any


class VcfNetworksError(RuntimeError):
    """Raised when the server response cannot be handled."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class VcfOperationsForNetworksClient:
    """Client for the contract's token and VM-list operations."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        domain: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.domain = domain
        self.timeout = timeout

    def list_vms(
        self,
        *,
        size: int | float | None = None,
        start_time: int | float | None = None,
        end_time: int | float | None = None,
    ) -> list[dict[str, Any]]:
        """Return every VM page, retaining progress across token renewal."""

        raise NotImplementedError("implement the VCF Operations for Networks calls")

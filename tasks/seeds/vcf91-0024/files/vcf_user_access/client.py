"""VCF 9.1 SDDC Manager user-access client."""

from __future__ import annotations

from typing import Any


class SddcManagerError(RuntimeError):
    """An SDDC Manager HTTP or transport failure."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: str,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.status_code = status_code
        self.payload = payload


class ProtocolError(SddcManagerError):
    """A successful response violated the protected contract."""


class AccessConflictError(SddcManagerError):
    """The requested identity already has a different role."""


class SddcManagerClient:
    """Client for the two operations named by the protected contract."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def list_users(self) -> list[dict[str, Any]]:
        """Return every observed user in deterministic order."""

        raise NotImplementedError

    def ensure_user_access(
        self,
        name: str,
        domain: str,
        principal_type: str,
        role_id: str,
    ) -> list[dict[str, Any]]:
        """Ensure one identity-to-role grant without duplicate mutation."""

        raise NotImplementedError

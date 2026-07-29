"""VCF 9.1 SDDC Manager domain inventory client."""

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


class SddcManagerClient:
    """Client for the three operations named by the protected contract."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def list_domains(
        self,
        *,
        page_size: int = 2,
    ) -> list[dict[str, Any]]:
        """Return every domain in deterministic order."""

        raise NotImplementedError

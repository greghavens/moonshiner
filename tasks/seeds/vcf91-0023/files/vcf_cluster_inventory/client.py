"""Standard-library SDDC Manager cluster inventory client."""

from __future__ import annotations

from typing import Any


class SddcManagerError(RuntimeError):
    """An HTTP or transport failure from an SDDC Manager operation."""

    def __init__(
        self,
        operation_id: str,
        status_code: int | None,
        payload: Any = None,
    ) -> None:
        self.operation_id = operation_id
        self.status_code = status_code
        self.payload = payload
        status = "transport failure" if status_code is None else f"HTTP {status_code}"
        super().__init__(f"{operation_id} failed: {status}")


class ProtocolError(RuntimeError):
    """A success response that violates the pinned OpenAPI contract."""

    def __init__(self, operation_id: str, message: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"{operation_id} protocol error: {message}")


class SddcManagerClient:
    """Client for the task-scoped SDDC Manager REST contract."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def list_clusters(
        self,
        *,
        page_size: int = 2,
    ) -> list[dict[str, Any]]:
        """Return every cluster in deterministic order."""

        raise NotImplementedError

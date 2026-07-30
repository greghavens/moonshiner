"""Standard-library vCenter authorization-role collection client."""

from __future__ import annotations

from typing import Any


OPERATION_ID = "Vcenter.Authorization.Roles_list"


class VcenterError(RuntimeError):
    """An HTTP or transport failure from a vCenter Automation operation."""

    def __init__(
        self,
        operation_id: str,
        status_code: int | None,
        payload: Any = None,
    ) -> None:
        self.operation_id = operation_id
        self.status_code = status_code
        self.payload = payload
        status = (
            "transport failure"
            if status_code is None
            else f"HTTP {status_code}"
        )
        super().__init__(f"{operation_id} failed: {status}")


class ProtocolError(RuntimeError):
    """An HTTP 200 response that violates the pinned OpenAPI contract."""

    def __init__(self, operation_id: str, message: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"{operation_id} protocol error: {message}")


class VcenterRoleClient:
    """Client for the task-scoped vCenter Automation REST contract."""

    def __init__(
        self,
        base_url: str,
        session_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def list_roles(
        self,
        *,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        """Return every authorization role in deterministic order."""

        raise NotImplementedError

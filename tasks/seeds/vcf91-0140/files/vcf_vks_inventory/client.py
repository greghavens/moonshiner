"""vSphere Supervisor namespace discovery and VKS Cluster inventory."""

from __future__ import annotations

from typing import Any


VCENTER_OPERATION_ID = "Vcenter.Namespaces.User.Instances_list"
KUBERNETES_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)


class VksInventoryError(RuntimeError):
    """An HTTP or transport failure."""

    def __init__(
        self,
        operation_id: str,
        status_code: int | None,
        payload: Any,
    ) -> None:
        self.operation_id = operation_id
        self.status_code = status_code
        self.payload = payload
        detail = (
            f" (HTTP {status_code})"
            if status_code is not None
            else ""
        )
        super().__init__(f"{operation_id} request failed{detail}")


class ProtocolError(RuntimeError):
    """A malformed successful response."""

    def __init__(self, operation_id: str, message: str) -> None:
        self.operation_id = operation_id
        super().__init__(f"{operation_id}: {message}")


class VksClusterInventoryClient:
    """Collect VKS Clusters discovered through a vSphere namespace."""

    def __init__(
        self,
        vcenter_url: str,
        vcenter_session_id: str,
        kubernetes_token: str,
        *,
        timeout: float = 10.0,
        kubernetes_scheme: str = "https",
    ) -> None:
        raise NotImplementedError

    def list_clusters(
        self,
        namespace: str,
        *,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        """Return the complete Cluster collection in stable order."""

        raise NotImplementedError

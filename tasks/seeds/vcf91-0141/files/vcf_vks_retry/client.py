"""Implement the contract-pinned VCF 9.1 retry workflow here."""

from __future__ import annotations

from collections.abc import Iterable


class ApiError(RuntimeError):
    """HTTP or transport failure."""


class ProtocolError(RuntimeError):
    """Malformed success response."""


class NamespaceNotReadyError(RuntimeError):
    """The Supervisor namespace is not RUNNING."""


class VksRetryClient:
    """Safely reconcile maintenance annotations on VKS Clusters."""

    def __init__(
        self,
        vcenter_url: str,
        supervisor_url: str,
        vcenter_session_id: str,
        kubernetes_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("complete VksRetryClient")

    def mark_clusters(
        self,
        *,
        namespace: str,
        cluster_names: Iterable[str],
        maintenance_id: str,
        note: str | None = None,
    ) -> list[dict[str, object]]:
        """Set the maintenance annotations and return final Cluster objects."""
        raise NotImplementedError("complete mark_clusters")

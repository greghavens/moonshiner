"""Implement the VCF 9.1 Supervisor-gated VKS change here."""

from __future__ import annotations


class ApiError(RuntimeError):
    """An HTTP operation failed."""


class ProtocolError(RuntimeError):
    """A successful response did not satisfy its contract."""


class PrecheckError(RuntimeError):
    """The namespace did not belong to the requested Supervisor."""


class GuardedClusterClient:
    """Patch one VKS Cluster only after its namespace passes precheck."""

    def __init__(
        self,
        vcenter_url: str,
        kubernetes_url: str,
        vcenter_session_id: str,
        kubernetes_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("complete GuardedClusterClient")

    def reconcile_version(
        self,
        *,
        supervisor: str,
        namespace: str,
        cluster_name: str,
        target_version: str,
    ) -> dict[str, object]:
        """Apply the version only when the Supervisor namespace is ready."""
        raise NotImplementedError("complete reconcile_version")

"""Implement the focused VCF 9.1 Supervisor-to-VKS workflow here."""

from __future__ import annotations


class ApiError(RuntimeError):
    """Transport, HTTP, or response-contract failure."""


class NamespaceFailedError(RuntimeError):
    """The Supervisor namespace reached an unsuccessful terminal state."""


class ClusterFailedError(RuntimeError):
    """The VKS Cluster resource reached an unsuccessful terminal state."""


class ProvisionTimeoutError(TimeoutError):
    """The shared namespace/cluster polling deadline expired."""


class VksProvisioner:
    """Provision a namespace and then a VKS cluster."""

    def __init__(
        self,
        vcenter_url: str,
        supervisor_url: str,
        session_id: str,
        bearer_token: str,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("complete VksProvisioner")

    def provision_cluster(self, **kwargs: object) -> dict[str, object]:
        """Create the namespace, wait, create the Cluster, and wait."""
        raise NotImplementedError("complete provision_cluster")

"""Implement the VCF 9.1 Supervisor/VKS coordinated change here."""

from __future__ import annotations


class ApiError(RuntimeError):
    """A REST operation failed before a reliable ledger could be returned."""


class ProtocolError(RuntimeError):
    """A successful HTTP response did not satisfy its response contract."""


class PreflightError(RuntimeError):
    """The named Supervisor namespace or VKS Cluster failed preflight."""


class CoordinatedChangeClient:
    """Apply one vCenter namespace change followed by one VKS change."""

    def __init__(
        self,
        vcenter_url: str,
        kubernetes_url: str,
        vcenter_session_id: str,
        kubernetes_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("complete CoordinatedChangeClient")

    def coordinate_change(
        self,
        *,
        supervisor: str,
        namespace: str,
        cluster_name: str,
        cluster_class: str,
        namespace_description: str,
        target_version: str,
    ) -> dict[str, object]:
        """Apply both changes and return a partial-success-aware ledger."""
        raise NotImplementedError("complete coordinate_change")

"""Implement the VCF 9.1 Supervisor/VKS session rotation here."""

from __future__ import annotations


class ApiError(RuntimeError):
    """An HTTP or transport operation failed."""


class ProtocolError(RuntimeError):
    """A successful response violated its operation contract."""


class RotatingVksClient:
    """Read VKS Clusters while safely rotating the vCenter session."""

    def __init__(
        self,
        vcenter_url: str,
        vcenter_session_id: str,
        kubernetes_token: str,
        *,
        kubernetes_scheme: str = "https",
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("complete RotatingVksClient")

    @property
    def session_generation(self) -> int:
        """Return the currently published session generation."""
        raise NotImplementedError("complete session_generation")

    def get_cluster(
        self,
        namespace: str,
        cluster_name: str,
    ) -> dict[str, object]:
        """Return one validated VKS Cluster summary."""
        raise NotImplementedError("complete get_cluster")

    def rotate_vcenter_session(
        self,
        username: str,
        password: str,
    ) -> int:
        """Publish a new session, drain the old generation, then delete it."""
        raise NotImplementedError("complete rotate_vcenter_session")

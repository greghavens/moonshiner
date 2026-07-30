"""HTTP clients for the focused vCenter and Kubernetes surfaces."""

from __future__ import annotations

from typing import Any


class DiagnosticError(RuntimeError):
    """Raised when evidence cannot be retrieved or trusted."""


class VCenterClient:
    """Client for the vCenter operations recorded in ``docs/contract.json``."""

    def __init__(self, base_url: str, session_id: str, timeout: float = 10.0):
        raise NotImplementedError("implement VCenterClient")

    def get_namespace(self, namespace: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_supervisor_summary(self, supervisor: str) -> dict[str, Any]:
        raise NotImplementedError


class KubernetesClient:
    """Small client for the core V1 Kubernetes evidence endpoints."""

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        raise NotImplementedError("implement KubernetesClient")

    def list_pods(
        self,
        namespace: str,
        *,
        label_selector: str | None = None,
        field_selector: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def list_events(
        self,
        namespace: str,
        *,
        label_selector: str | None = None,
        field_selector: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_pod_log(
        self,
        namespace: str,
        pod: str,
        *,
        container: str | None = None,
        tail_lines: int | None = None,
        previous: bool | None = None,
        timestamps: bool | None = None,
    ) -> str:
        raise NotImplementedError

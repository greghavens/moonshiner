"""Evidence collection and diagnosis orchestration."""

from __future__ import annotations

from typing import Any

from .client import DiagnosticError, KubernetesClient, VCenterClient


def diagnose_workload(
    vcenter: VCenterClient,
    kubernetes: KubernetesClient,
    namespace: str,
    label_selector: str,
    *,
    container: str | None = None,
    tail_lines: int = 200,
) -> dict[str, Any]:
    """Collect namespace, pod, Event, and log evidence."""

    raise NotImplementedError

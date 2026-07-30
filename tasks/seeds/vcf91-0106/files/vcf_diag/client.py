"""Client implementation for the focused VCF 9.1 evidence contract."""

from __future__ import annotations

from typing import Any, Iterable


class VCenterAPIError(RuntimeError):
    """Raised when a vCenter API request or response is unusable."""


class VCenterClient:
    """Client for the operations recorded in ``docs/contract.json``."""

    def __init__(self, base_url: str, session_id: str, timeout: float = 10.0):
        raise NotImplementedError("implement VCenterClient")

    def list_tpms(
        self,
        host: str,
        *,
        active: bool | None = None,
        major_versions: Iterable[int] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_tpm_event_log(self, host: str, tpm: str) -> dict[str, Any]:
        raise NotImplementedError

    def create_log_bundle(
        self,
        description: str,
        *,
        components: dict[str, list[str]] | None = None,
        partition: str | None = None,
    ) -> str:
        raise NotImplementedError


def collect_diagnosis(
    client: VCenterClient, host: str, description: str
) -> dict[str, Any]:
    """Collect TPM and support-bundle evidence and return a diagnosis report."""

    raise NotImplementedError

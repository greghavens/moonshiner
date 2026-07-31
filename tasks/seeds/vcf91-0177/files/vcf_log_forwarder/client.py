"""VCF Operations Log Management HTTP client.

Implement this module using only the Python standard library and the pinned
contract in docs/contract.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ForwarderSpec:
    """Writable fields from the contract's LogForwarder schema."""

    certificate: str | None = None
    connection_refresh_interval: int | None = None
    constraints: Mapping[str, Any] | None = None
    enabled: bool | None = None
    forward_complementary_fields: bool | None = None
    host: str | None = None
    name: str | None = None
    port: int | None = None
    protocol: str | None = None
    ssl_enabled: bool | None = None
    tags: Mapping[str, str] | None = None
    transport_protocol: str | None = None
    worker_count: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return writable values under their contract-defined wire names."""

        raise NotImplementedError


@dataclass(frozen=True)
class ApiResponse:
    operation_id: str
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class VcfLogClient:
    """Minimal client for the two operations selected in docs/contract.json."""

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        raise NotImplementedError

    def create_log_forwarder(self, spec: ForwarderSpec) -> ApiResponse:
        raise NotImplementedError

    def patch_log_forwarder(
        self, forwarder_id: str, changes: Mapping[str, Any]
    ) -> ApiResponse:
        raise NotImplementedError

"""Client types for the pinned VCF Operations for Logs 9.0 contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class Forwarder:
    """A log-forwarding destination accepted by ``POST_log-forwarder``."""

    name: str
    host: str
    port: int
    protocol: str
    ssl_enabled: bool
    accept_cert: Optional[bool] = None
    worker_count: Optional[int] = None
    connection_refresh_interval: Optional[int] = None
    disk_cache_size: Optional[int] = None
    tags: Optional[Mapping[str, str]] = None
    filter: Optional[str] = None
    transport_protocol: Optional[str] = None
    forward_complementary_fields: Optional[bool] = None
    test_connection: Optional[bool] = None

    def as_request(self) -> dict[str, object]:
        """Return the JSON object for ``POST_log-forwarder``."""

        raise NotImplementedError("implement the VCF 9.0 request mapping")


class VCFLogsClient:
    """Small standard-library client for the two operations in the contract."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def configure_forwarders(
        self,
        username: str,
        password: str,
        forwarders: Iterable[Forwarder],
        provider: str = "Local",
    ) -> dict[str, object]:
        """Authenticate and create each forwarding destination in order."""

        raise NotImplementedError("implement the VCF 9.0 integration")

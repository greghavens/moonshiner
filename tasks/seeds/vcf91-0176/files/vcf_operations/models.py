"""Request models for the focused Log Management contract."""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class LogForwarderUpdate:
    """Writable fields of the OpenAPI ``LogForwarder`` schema.

    ``None`` means that a property was not set and must be omitted. Other falsy
    values are intentional values and must remain on the wire.
    """

    certificate: Optional[str] = None
    connection_refresh_interval: Optional[int] = None
    constraints: Optional[Mapping[str, Any]] = None
    enabled: Optional[bool] = None
    forward_complementary_fields: Optional[bool] = None
    host: Optional[str] = None
    name: Optional[str] = None
    port: Optional[int] = None
    protocol: Optional[str] = None
    ssl_enabled: Optional[bool] = None
    tags: Optional[Mapping[str, str]] = None
    transport_protocol: Optional[str] = None
    worker_count: Optional[int] = None

    def to_wire(self) -> Dict[str, Any]:
        """Return a contract-shaped request object."""

        raise NotImplementedError

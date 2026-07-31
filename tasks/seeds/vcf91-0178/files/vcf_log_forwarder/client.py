"""Standard-library client for a gated VCF log-forwarder creation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ForwarderConfig:
    """Focused writable projection of the OpenAPI LogForwarder schema."""

    name: str
    host: str
    port: int
    protocol: str
    ssl_enabled: bool
    transport_protocol: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class CreateResult:
    created: bool
    operation_id: str
    id: str


class LogManagementError(Exception):
    """Base error for the focused client."""


class ApiError(LogManagementError):
    """An operation returned a status other than its contract success status."""

    def __init__(self, operation_id: str, status_code: int) -> None:
        self.operation_id = operation_id
        self.status_code = status_code
        super().__init__(f"{operation_id} returned HTTP {status_code}")


class PrecheckFailed(ApiError):
    """The connection test failed, so creation was not attempted."""


class ProtocolError(LogManagementError):
    """A success response did not match the focused contract."""


class LogManagementClient:
    """Create a log forwarder only after its connection precheck passes."""

    def __init__(
        self,
        base_url: str,
        log_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError("TODO: validate and initialize the client")

    def create_after_precheck(
        self,
        forwarder: ForwarderConfig,
    ) -> CreateResult:
        raise NotImplementedError("TODO: implement the gated workflow")

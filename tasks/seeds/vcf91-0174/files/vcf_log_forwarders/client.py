"""VCF Operations Log Management forwarder client."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Callable


class ApiError(RuntimeError):
    """An unexpected response from the focused Log Management API."""

    def __init__(
        self,
        status_code: int,
        *,
        error_code: str | None = None,
        message: str | None = None,
        payload: Any = None,
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.payload = payload
        details = [f"VCF Log Management request failed with HTTP {status_code}"]
        if error_code:
            details.append(error_code)
        if message:
            details.append(message)
        super().__init__(": ".join(details))


class TokenProviderError(ValueError):
    """The access-token provider returned an unusable value."""


class LogManagementClient:
    """Client for the focused log-forwarder reconciliation contract."""

    LOG_FORWARDER_PROPERTIES = (
        "certificate",
        "connectionRefreshInterval",
        "constraints",
        "enabled",
        "forwardComplementaryFields",
        "host",
        "id",
        "name",
        "port",
        "protocol",
        "sslEnabled",
        "tags",
        "transportProtocol",
        "workerCount",
    )

    def __init__(
        self,
        base_url: str,
        access_token_provider: Callable[[bool], str],
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def list_forwarders(self) -> list[dict[str, Any]]:
        """Return the complete current log-forwarder collection."""
        raise NotImplementedError

    def create_forwarder(
        self, forwarder: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Create one schema-projected log forwarder."""
        raise NotImplementedError

    def reconcile_forwarders(
        self, desired_forwarders: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """Preserve existing forwarders and create missing names in input order."""
        raise NotImplementedError

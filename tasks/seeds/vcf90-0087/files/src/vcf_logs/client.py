"""VCF Operations for Logs 9.0 HTTP client."""

from __future__ import annotations

from typing import Any, Sequence


class LogsApiError(RuntimeError):
    """An HTTP response that the client cannot recover from."""

    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        super().__init__(f"VCF Operations for Logs returned HTTP {status}: {payload!r}")


class LogsClient:
    """Client for the operations named by ``docs/contract.json``.

    ``base_url`` is the service origin. The contract's ``/api/v2`` base path is
    appended by this client.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        provider: str = "Local",
        *,
        request_timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.provider = provider
        self.request_timeout = request_timeout
        self._session_id: str | None = None

    def query_events(
        self,
        path: str,
        *,
        limit: int | None = None,
        timeout: int | None = None,
        view: str | None = None,
        content_pack_fields: Sequence[str] | None = None,
        order_by_direction: str | None = None,
    ) -> dict[str, Any]:
        """Execute ``GET_events-+path`` and return its decoded JSON object."""
        raise NotImplementedError("implement the contract-backed event query")

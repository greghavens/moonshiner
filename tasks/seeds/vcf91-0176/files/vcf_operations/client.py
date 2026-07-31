"""HTTP client for the focused VCF Operations Log Management contract."""

from typing import Any, Dict

from .models import LogForwarderUpdate


class LogManagementError(RuntimeError):
    """Raised when a Log Management operation cannot be completed."""

    def __init__(self, message: str, *, status: int = None, body: bytes = b""):
        super().__init__(message)
        self.status = status
        self.body = body


class LogManagementClient:
    """Client for the contract's ``updateLogForwarder`` operation."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 5.0,
        max_attempts: int = 2,
    ):
        if not base_url:
            raise ValueError("base_url is required")
        if not token:
            raise ValueError("token is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_attempts = max_attempts

    def update_log_forwarder(
        self,
        forwarder_id: str,
        update: LogForwarderUpdate,
    ) -> Dict[str, Any]:
        """Replace a log forwarder using an idempotent PUT."""

        raise NotImplementedError

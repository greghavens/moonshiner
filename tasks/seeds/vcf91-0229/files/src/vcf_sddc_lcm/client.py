"""VCF SDDC LCM support-bundle client implementation exercise."""

from __future__ import annotations


class SddcLcmError(RuntimeError):
    """Raised when an SDDC LCM request or response is invalid."""


class SddcLcmClient:
    """Small focused client for the protected SDDC LCM contract."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url
        self._access_token = access_token
        self._timeout = timeout

    def ensure_support_bundle(
        self,
        component_id: str,
        correlation_id: str,
        *,
        look_back_window: int | None = None,
        page_size: int = 50,
    ) -> dict[str, object]:
        """Generate a component support bundle at most once per correlation ID."""
        raise NotImplementedError("implement the retry-safe support-bundle workflow")

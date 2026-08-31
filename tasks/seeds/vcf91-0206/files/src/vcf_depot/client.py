"""VCF 9.1 SDDC Manager external-services client.

Implement the public classes according to README.md and docs/contract.json.
Only Python's standard library may be used.
"""

from __future__ import annotations


class SddcManagerError(RuntimeError):
    """Raised when an SDDC Manager request cannot be completed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SddcManagerClient:
    """Client for the contract's retry-safe services-config mutation."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        timeout: float = 10.0,
        max_attempts: int = 2,
    ) -> None:
        raise NotImplementedError("Implement SddcManagerClient")

    def update_services_config(
        self,
        services: list[dict[str, object]],
    ) -> dict[str, object]:
        """Replace external service connections using updateServicesConfig."""
        raise NotImplementedError("Implement update_services_config")

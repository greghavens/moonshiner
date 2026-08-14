"""HTTP client for the operations listed in docs/contract.json."""

from __future__ import annotations

from typing import Any, Mapping


JsonObject = dict[str, Any]


class VcfAutomationError(RuntimeError):
    """Raised when transport or response handling prevents an API operation."""


class VcfAutomationClient:
    """Bearer-authenticated VCF Automation deployment client.

    ``None`` means an optional request property is unset. Explicit non-None
    values, including an empty ``inputs`` mapping, are values supplied by the
    caller and are serialized.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def patch_deployment(
        self,
        deployment_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        icon_id: str | None = None,
    ) -> JsonObject:
        """Update the set deployment metadata fields."""
        raise NotImplementedError

    def submit_deployment_action(
        self,
        deployment_id: str,
        action_id: str,
        *,
        inputs: Mapping[str, Any] | None = None,
        reason: str | None = None,
    ) -> JsonObject:
        """Submit one deployment action request."""
        raise NotImplementedError

    def get_request(self, request_id: str) -> JsonObject:
        """Fetch the current state of an action request."""
        raise NotImplementedError

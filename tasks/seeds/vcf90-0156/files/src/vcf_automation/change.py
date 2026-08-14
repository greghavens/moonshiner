"""Multi-step deployment metadata and action change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .client import JsonObject, VcfAutomationClient


@dataclass(frozen=True)
class DeploymentChangeResult:
    """Complete report for a deployment change, including partial success."""

    deployment_id: str
    patch_succeeded: bool
    patched_deployment: JsonObject | None
    action_submitted: bool
    action_request: JsonObject | None
    final_request: JsonObject | None

    @property
    def request_id(self) -> str | None:
        value = (self.action_request or {}).get("id")
        return value if isinstance(value, str) else None

    @property
    def status(self) -> str | None:
        value = (self.final_request or {}).get("status")
        return value if isinstance(value, str) else None

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCESSFUL"

    @property
    def failure_details(self) -> str | None:
        if self.succeeded:
            return None
        value = (self.final_request or {}).get("details")
        return value if isinstance(value, str) else None


def apply_deployment_change(
    client: VcfAutomationClient,
    deployment_id: str,
    action_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    icon_id: str | None = None,
    inputs: Mapping[str, Any] | None = None,
    reason: str | None = None,
    poll_interval: float = 1.0,
) -> DeploymentChangeResult:
    """Patch metadata, submit an action, and report its terminal result."""
    raise NotImplementedError

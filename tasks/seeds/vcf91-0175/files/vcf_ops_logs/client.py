"""VCF Operations Log Management agent-group client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentGroup:
    """Stable projection of an agent-group response object."""

    id: str
    name: str
    auto_update: bool
    info: str


class ApiError(RuntimeError):
    """An HTTP or transport failure from the focused Log Management API."""


class ResponseContractError(RuntimeError):
    """A successful response did not match the focused contract."""


class LogManagementClient:
    """Client for ``getAllAgentGroupConfig``."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def list_all_agent_groups(
        self,
        *,
        page_size: int = 100,
    ) -> list[AgentGroup]:
        """Return the complete agent-group collection in stable order."""
        raise NotImplementedError

"""Client surface for the VCF Operations Log Management API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class AgentSecret:
    """An agent secret and its provisioning status."""

    id: str
    name: str
    status: str
    secret: str | None = None
    modification_time: str | None = None


class ApiError(RuntimeError):
    """A non-success response from the Log Management API."""


class ProvisioningTimeout(TimeoutError):
    """The secret did not reach a terminal state before the deadline."""


class ProvisioningFailed(RuntimeError):
    """The secret reached an unsuccessful terminal state."""


class LogManagementClient:
    """Minimal client for the two operations in ``docs/contract.json``."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def create_agent_secret(self, name: str | None = None) -> AgentSecret:
        raise NotImplementedError

    def list_agent_secrets(
        self,
        *,
        page: int = 0,
        size: int = 100,
        sort: Sequence[str] | None = None,
    ) -> AgentSecret:
        raise NotImplementedError

    def provision_agent_secret(
        self,
        name: str | None = None,
        *,
        poll_interval: float = 0.0,
        timeout: float = 5.0,
    ) -> AgentSecret:
        raise NotImplementedError

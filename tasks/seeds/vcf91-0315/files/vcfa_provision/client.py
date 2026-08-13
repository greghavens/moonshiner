"""Client for the VCF Automation provisioning service.

Unimplemented. See ``README.md`` for the required behaviour and
``docs/contract.json`` for the wire contract this client is pinned to.
"""

import time

from .models import MachineSpec, ProvisionResult

__all__ = ["VcfAutomationClient"]


class VcfAutomationClient:
    """Submits provisioning requests and drives them to a terminal state."""

    def __init__(self, base_url, token, api_version, poll_interval=5.0,
                 max_poll_attempts=60, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_version = api_version
        self.poll_interval = poll_interval
        self.max_poll_attempts = max_poll_attempts
        self.sleep = sleep

    def provision_machine(self, spec: MachineSpec) -> ProvisionResult:
        """Provision a machine and return once the request is terminal."""
        raise NotImplementedError

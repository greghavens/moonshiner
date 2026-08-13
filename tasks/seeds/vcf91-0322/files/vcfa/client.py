"""Client for the VCF Automation deployment APIs named in ``docs/contract.json``.

Each method below maps onto exactly one contract operation; the operation id is given in
the method docstring. Read the contract before implementing a method: it lists every
query parameter and body field the operation accepts, and it is the authority on which
of them belong on the wire for a given call.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import VcfaApiError, VcfaError
from .transport import Response, request


class VcfAutomationClient:
    """Talks to one VCF Automation appliance on behalf of one tenant."""

    def __init__(self, base_url: str, tenant: str, api_token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.tenant = tenant
        self.api_token = api_token
        self.timeout = timeout
        self.access_token: Optional[str] = None

    # -- authentication ---------------------------------------------------

    def authenticate(self) -> str:
        """Contract operation ``auth.token.exchange``.

        Exchange ``self.api_token`` for a bearer access token, store it on
        ``self.access_token`` and return it.
        """
        raise NotImplementedError

    # -- reads ------------------------------------------------------------

    def find_deployment(self, name: str) -> Optional[Dict[str, Any]]:
        """Contract operation ``deployments.list``.

        Return the deployment whose name is exactly ``name``, or None if there is none.
        """
        raise NotImplementedError

    def list_deployment_requests(self, deployment_id: str) -> List[Dict[str, Any]]:
        """Contract operation ``deployments.requests.list``.

        Return the request records the deployment carries, newest first.
        """
        raise NotImplementedError

    def get_request(self, request_id: str) -> Dict[str, Any]:
        """Contract operation ``requests.get``. Return the full request record."""
        raise NotImplementedError

    def list_request_events(self, request_id: str) -> List[Dict[str, Any]]:
        """Contract operation ``requests.events.list``.

        Return the events recorded against the request, oldest first.
        """
        raise NotImplementedError

    def get_event_logs(
        self, request_id: str, event_id: str, since_row: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Contract operation ``requests.events.logs.get``.

        Return the event's log entries ordered by ``rownum``. ``since_row`` is optional
        and, when the caller does not supply one, must not reach the wire.
        """
        raise NotImplementedError

    def list_deployment_resources(self, deployment_id: str) -> List[Dict[str, Any]]:
        """Contract operation ``deployments.resources.list``.

        Return every resource that belongs to the deployment.
        """
        raise NotImplementedError

    # -- writes -----------------------------------------------------------

    def dismiss_request(self, request_id: str) -> None:
        """Contract operation ``requests.action`` with the dismiss action."""
        raise NotImplementedError

    def submit_deployment_action(
        self,
        deployment_id: str,
        action_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Contract operation ``deployments.requests.submitAction``.

        Submit a day-2 action against the deployment and return the created request.
        ``inputs`` and ``reason`` are optional; an argument the caller did not supply,
        and an ``inputs`` mapping that is empty, must not reach the wire.
        """
        raise NotImplementedError

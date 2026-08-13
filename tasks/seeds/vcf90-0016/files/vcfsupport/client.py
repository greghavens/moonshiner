"""HTTP client for the SDDC Manager operations named by docs/contract.json.

One method per contract operationId. Nothing here may import a third-party
package: the whole client is built on the Python standard library.
"""

from __future__ import annotations


class SddcManagerError(Exception):
    """Raised when SDDC Manager returns an error response."""

    def __init__(self, status: int, payload) -> None:
        super().__init__(f"HTTP {status}: {payload}")
        self.status = status
        self.payload = payload


class SddcManagerClient:
    """Client for a single SDDC Manager appliance.

    ``base_url`` is the scheme and authority of the appliance, for example
    ``https://sfo-vcf01.rainpole.io``.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token: str | None = None

    # -- Tokens -----------------------------------------------------------
    def create_token(self, username: str, password: str) -> dict:
        """createToken: POST /v1/tokens. Stores and returns the token pair."""
        raise NotImplementedError

    # -- Tasks ------------------------------------------------------------
    def get_tasks(self, **filters) -> dict:
        """getTasks: GET /v1/tasks. Only the filters passed here are sent."""
        raise NotImplementedError

    def get_task(self, task_id: str) -> dict:
        """getTask: GET /v1/tasks/{id}."""
        raise NotImplementedError

    def retry_task(self, task_id: str) -> None:
        """retryTask: PATCH /v1/tasks/{id}."""
        raise NotImplementedError

    # -- Notifications ----------------------------------------------------
    def get_notifications(self) -> list:
        """getNotifications: GET /v1/notifications."""
        raise NotImplementedError

    # -- SOS --------------------------------------------------------------
    def start_support_bundle(self, spec: dict) -> dict:
        """startSupportBundle: POST /v1/system/support-bundles."""
        raise NotImplementedError

    def get_support_bundle_status(self, bundle_id: str) -> dict:
        """getSupportBundleStatus: GET /v1/system/support-bundles/{id}."""
        raise NotImplementedError

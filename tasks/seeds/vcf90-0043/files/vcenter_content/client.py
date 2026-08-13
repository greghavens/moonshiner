"""Client for the three vSphere Automation operations in docs/contract.json.

Implement the bodies below. Only the Python standard library may be used.
"""

from __future__ import annotations


class VCenterApiError(Exception):
    """An error response from the vCenter Automation API.

    ``status`` is the HTTP status code. ``error_type`` and ``messages`` come from
    the Vapi.Std.Errors.Error body when the response carried one.
    """

    def __init__(self, status, error_type=None, messages=None, detail=None):
        super().__init__(detail or "%s %s" % (status, error_type or ""))
        self.status = status
        self.error_type = error_type
        self.messages = messages or []


class ContentLibraryClient:
    """Talks to a vCenter appliance's ``/api`` endpoint.

    Args:
        base_url: appliance API root, e.g. ``https://vcenter.example.com/api``.
        username: user for ``Cis.Session_create`` HTTP Basic auth.
        password: password for that user.
        max_attempts: total attempts for a retryable request (1 disables retry).
        backoff: seconds to wait between attempts.
        timeout: per-request socket timeout in seconds.
    """

    def __init__(
        self,
        base_url,
        username,
        password,
        max_attempts=3,
        backoff=0.0,
        timeout=10.0,
    ):
        raise NotImplementedError

    def ensure_library_item(
        self,
        library_id,
        name,
        description=None,
        item_type=None,
        client_token=None,
    ):
        """Create a content library item, retrying safely on transient failure.

        Returns the identifier of the library item as a string.
        """
        raise NotImplementedError

    def get_library_item(self, item_id):
        """Return the Content.Library.ItemModel for ``item_id`` as a dict."""
        raise NotImplementedError

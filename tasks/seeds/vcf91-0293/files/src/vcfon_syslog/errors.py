"""Error type for appliance responses that carry an ApiError body."""

from __future__ import annotations


class VcfOnApiError(Exception):
    """A non-2xx response from the appliance.

    ``code`` and ``message`` come from the ApiError body when the appliance sent
    one; they fall back to the HTTP status and reason phrase when it did not.
    """

    def __init__(self, http_status, code, message, details=None):
        super().__init__("HTTP %s: %s (code %s)" % (http_status, message, code))
        self.http_status = http_status
        self.code = code
        self.message = message
        self.details = details or []

    @classmethod
    def from_response(cls, response):
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            http_status=response.status,
            code=payload.get("code", response.status),
            message=payload.get("message", ""),
            details=payload.get("details"),
        )

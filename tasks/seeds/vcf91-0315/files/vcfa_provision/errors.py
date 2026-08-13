"""Exceptions raised by :mod:`vcfa_provision`."""

from typing import Any, Dict, Optional

__all__ = ["VcfAutomationError", "ApiError", "ProvisioningFailed", "ProvisioningTimeout"]


class VcfAutomationError(Exception):
    """Base class for every error raised by this package."""


class ApiError(VcfAutomationError):
    """The service answered with an unexpected HTTP status."""

    def __init__(self, status_code: int, body: Any = None, message: Optional[str] = None):
        self.status_code = status_code
        self.body = body
        text = message or "VCF Automation API returned HTTP {0}".format(status_code)
        super().__init__(text)


class ProvisioningFailed(VcfAutomationError):
    """The request reached a terminal state that is not success."""

    def __init__(self, request_id: str, message: Optional[str] = None,
                 tracker: Optional[Dict[str, Any]] = None):
        self.request_id = request_id
        self.message = message
        self.tracker = tracker or {}
        text = "request {0} failed".format(request_id)
        if message:
            text = "{0}: {1}".format(text, message)
        super().__init__(text)


class ProvisioningTimeout(VcfAutomationError):
    """The request did not reach a terminal state within the allowed polls."""

    def __init__(self, request_id: str, attempts: int,
                 tracker: Optional[Dict[str, Any]] = None):
        self.request_id = request_id
        self.attempts = attempts
        self.tracker = tracker or {}
        super().__init__(
            "request {0} was still not terminal after {1} poll(s)".format(request_id, attempts)
        )

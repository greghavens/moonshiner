"""Public API for the SDDC Manager cluster inventory package."""

from .client import ProtocolError, SddcManagerClient, SddcManagerError

__all__ = [
    "ProtocolError",
    "SddcManagerClient",
    "SddcManagerError",
]

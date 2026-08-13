"""Exception types raised by the VCF Automation client."""

from __future__ import annotations


class VcfaError(Exception):
    """Base class for every error raised by this package."""


class VcfaApiError(VcfaError):
    """A VCF Automation endpoint answered with a non-success status."""

    def __init__(self, status: int, method: str, url: str, payload=None):
        self.status = status
        self.method = method
        self.url = url
        self.payload = payload
        detail = ""
        if isinstance(payload, dict):
            detail = " " + ", ".join(f"{k}={v!r}" for k, v in sorted(payload.items()))
        elif payload:
            detail = " " + str(payload)
        super().__init__(f"{method} {url} -> HTTP {status}.{detail}")

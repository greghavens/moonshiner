"""Retry-safe SDDC Manager credential rotation for VMware Cloud Foundation 9.0."""

from .client import SddcManagerClient, SddcManagerError

__all__ = ["SddcManagerClient", "SddcManagerError"]

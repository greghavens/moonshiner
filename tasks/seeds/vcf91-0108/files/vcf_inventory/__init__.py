"""Focused stdlib client for a contract-pinned vCenter inventory snapshot."""

from .client import ProtocolError, VcenterError, VcenterInventoryClient

__all__ = [
    "ProtocolError",
    "VcenterError",
    "VcenterInventoryClient",
]

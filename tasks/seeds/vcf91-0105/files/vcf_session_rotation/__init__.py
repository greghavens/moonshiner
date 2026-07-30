"""Focused VMware vCenter client with drain-safe credential rotation."""

from .client import ProtocolError, RotatingVcenterClient, VcenterError

__all__ = [
    "ProtocolError",
    "RotatingVcenterClient",
    "VcenterError",
]

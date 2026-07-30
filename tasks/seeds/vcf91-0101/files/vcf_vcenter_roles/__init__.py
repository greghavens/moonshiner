"""Public API for the task-scoped vCenter role collection client."""

from .client import ProtocolError, VcenterError, VcenterRoleClient

__all__ = (
    "ProtocolError",
    "VcenterError",
    "VcenterRoleClient",
)

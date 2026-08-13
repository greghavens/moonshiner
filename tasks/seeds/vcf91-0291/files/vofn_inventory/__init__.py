"""Standard-library-only client package for VCF Operations for Networks 9.1.

The package exposes a single focused workflow: collect the complete Kubernetes
pod inventory of a VCF Operations for Networks appliance and emit it in a
stable order. Only the operations named in ``docs/contract.json`` are used.
"""

from .client import (
    BASE_PATH,
    LIST_OPERATION_ID,
    NAMES_OPERATION_ID,
    APIError,
    InventoryError,
    InventoryOptions,
    NetworksClient,
    PodRecord,
    ProtocolError,
    TransportError,
    render_inventory,
)

__all__ = [
    "BASE_PATH",
    "LIST_OPERATION_ID",
    "NAMES_OPERATION_ID",
    "APIError",
    "InventoryError",
    "InventoryOptions",
    "NetworksClient",
    "PodRecord",
    "ProtocolError",
    "TransportError",
    "render_inventory",
]

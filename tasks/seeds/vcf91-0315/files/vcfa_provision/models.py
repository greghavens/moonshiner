"""Request models for the VCF Automation provisioning client.

Attribute names here are fixed; the mapping from these attributes onto the
documented wire property names lives in :mod:`vcfa_provision.client`.

Every attribute that defaults to ``None`` is optional and, when left unset, must
not appear in the serialized request body at all.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    "Tag",
    "NetworkInterfaceSpec",
    "DiskSpec",
    "Constraint",
    "MachineSpec",
    "ProvisionResult",
]


@dataclass(frozen=True)
class Tag:
    """A key/value tag."""

    key: str
    value: str


@dataclass(frozen=True)
class NetworkInterfaceSpec:
    """One entry of a machine's network interface collection."""

    network_id: str
    device_index: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    fabric_network_id: Optional[str] = None
    addresses: Optional[List[str]] = None
    mac_address: Optional[str] = None
    security_group_ids: Optional[List[str]] = None
    custom_properties: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class DiskSpec:
    """One entry of a machine's disk collection."""

    block_device_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    scsi_controller: Optional[str] = None
    unit_number: Optional[str] = None
    disk_attachment_properties: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class Constraint:
    """A placement constraint."""

    expression: str
    mandatory: Optional[bool] = None


@dataclass(frozen=True)
class MachineSpec:
    """Everything needed to ask the provisioning service for a machine."""

    name: str
    project_id: str
    flavor: str
    flavor_ref: str
    image: str
    image_ref: str
    description: Optional[str] = None
    deployment_id: Optional[str] = None
    machine_count: Optional[int] = None
    custom_properties: Optional[Dict[str, str]] = None
    nics: Optional[List[NetworkInterfaceSpec]] = None
    disks: Optional[List[DiskSpec]] = None
    tags: Optional[List[Tag]] = None
    boot_config_content: Optional[str] = None
    constraints: Optional[List[Constraint]] = None


@dataclass
class ProvisionResult:
    """Outcome of a completed provisioning request."""

    request_id: str
    machine_id: str
    machine: Dict[str, Any] = field(default_factory=dict)
    tracker: Dict[str, Any] = field(default_factory=dict)
    poll_count: int = 0

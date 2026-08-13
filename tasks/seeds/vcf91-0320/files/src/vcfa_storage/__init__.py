"""vcfa-storage-profile: reconcile VCF Automation vSphere storage classes.

Speaks the three operations named in ``docs/contract.json``:

* ``getRegion``                   GET  /iaas/api/regions/{id}
* ``getFabricVsphereDatastore``   GET  /iaas/api/fabric-vsphere-datastores/{id}
* ``createVsphereStorageProfile`` POST /iaas/api/storage-profiles-vsphere

Standard library only.
"""

from .client import StorageProfileClient
from .errors import (
    ApiError,
    DatastoreNotFoundError,
    PlacementMismatchError,
    PrecheckFailed,
    RegionNotFoundError,
    VcfAutomationError,
)

__all__ = [
    "StorageProfileClient",
    "ApiError",
    "DatastoreNotFoundError",
    "PlacementMismatchError",
    "PrecheckFailed",
    "RegionNotFoundError",
    "VcfAutomationError",
]

"""HTTP client for the three VCF Automation operations in ``docs/contract.json``.

Read ``docs/contract.json`` before changing this file. It is the authority for
paths, methods, headers, query parameters, body fields and the placement
precheck, and it marks which parts are literal transcription of the Broadcom
xAPIs reference and which parts are inference.

Standard library only. The only host this client may contact is the
``base_url`` it was constructed with.
"""

from .errors import (  # noqa: F401
    ApiError,
    DatastoreNotFoundError,
    PlacementMismatchError,
    PrecheckFailed,
    RegionNotFoundError,
    VcfAutomationError,
)

DEFAULT_TIMEOUT = 30.0

REGIONS_PATH = "/iaas/api/regions"
DATASTORES_PATH = "/iaas/api/fabric-vsphere-datastores"
STORAGE_PROFILES_VSPHERE_PATH = "/iaas/api/storage-profiles-vsphere"

#: Keyword argument -> StorageProfileVsphereSpecification field, for the
#: optional half of the create body. Every one of these is omitted from the
#: JSON object when the caller leaves it at ``None``.
OPTIONAL_BODY_FIELDS = (
    ("description", "description"),
    ("supports_encryption", "supportsEncryption"),
    ("tags", "tags"),
    ("datastore_id", "datastoreId"),
    ("storage_policy_id", "storagePolicyId"),
    ("provisioning_type", "provisioningType"),
    ("limit_iops", "limitIops"),
    ("disk_mode", "diskMode"),
    ("disk_type", "diskType"),
    ("priority", "priority"),
    ("storage_filter_type", "storageFilterType"),
    ("tags_to_match", "tagsToMatch"),
    ("compute_host_id", "computeHostId"),
)


class StorageProfileClient:
    """Talks to one VCF Automation appliance.

    :param base_url: Scheme and authority of the appliance, no trailing slash.
    :param access_token: Bearer token presented in the ``Authorization`` header.
    :param api_version: Value for the optional ``apiVersion`` query parameter,
        in ``yyyy-MM-dd`` form. When ``None`` the parameter must not appear in
        the query string at all.
    :param timeout: Socket timeout in seconds, passed through to urllib.
    """

    def __init__(self, base_url, access_token, api_version=None, timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.api_version = api_version
        self.timeout = timeout

    # -- reads ---------------------------------------------------------------

    def get_region(self, region_id):
        """Call ``getRegion`` and return the decoded ``Region``.

        Returns ``None`` when the appliance answers 404. Raises
        :class:`ApiError` on any other non-2xx status.
        """
        raise NotImplementedError("get_region is not implemented yet")

    def get_datastore(self, datastore_id):
        """Call ``getFabricVsphereDatastore`` and return the decoded datastore.

        Returns ``None`` when the appliance answers 404. Raises
        :class:`ApiError` on any other non-2xx status.
        """
        raise NotImplementedError("get_datastore is not implemented yet")

    # -- gated mutation ------------------------------------------------------

    def create_vsphere_storage_profile(
        self,
        name,
        region_id,
        default_item,
        description=None,
        supports_encryption=None,
        tags=None,
        datastore_id=None,
        storage_policy_id=None,
        provisioning_type=None,
        limit_iops=None,
        disk_mode=None,
        disk_type=None,
        priority=None,
        storage_filter_type=None,
        tags_to_match=None,
        compute_host_id=None,
    ):
        """Run the contract's placement precheck, then create the storage class.

        ``name``, ``region_id`` and ``default_item`` are the three fields the
        contract marks required. Everything after them is optional: a value of
        ``None`` means the caller did not supply the field, and the field must
        then be absent from the request body.

        Returns the decoded ``VsphereStorageProfile`` from the 201 response.

        Raises a :class:`PrecheckFailed` subclass when the precheck refuses,
        in which case no mutating request has been sent. Raises
        :class:`ApiError` when the create itself fails.
        """
        raise NotImplementedError("create_vsphere_storage_profile is not implemented yet")

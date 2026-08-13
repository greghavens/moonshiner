"""Exceptions raised by :mod:`vcfa_storage`.

Standard library only.
"""


class VcfAutomationError(Exception):
    """Base class for every error this package raises."""


class ApiError(VcfAutomationError):
    """A VCF Automation operation answered with an unexpected status.

    ``status`` is the HTTP status code. ``body`` is the decoded
    ``ServiceErrorResponse`` when the appliance sent one and the raw text
    otherwise; it may be ``None`` when there was no body at all.
    """

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class PrecheckFailed(VcfAutomationError):
    """Base class for the placement precheck refusing to proceed.

    Raising any subclass of this is a promise that no mutating request was
    sent, so nothing on the appliance was changed.
    """


class RegionNotFoundError(PrecheckFailed):
    """The requested ``region_id`` does not resolve on this appliance."""


class DatastoreNotFoundError(PrecheckFailed):
    """The requested ``datastore_id`` does not resolve on this appliance."""


class PlacementMismatchError(PrecheckFailed):
    """The datastore does not line up with the region.

    Raised when the datastore sits in a different datacenter than the region
    points at, or when the region's cloud account cannot reach the datastore.
    """

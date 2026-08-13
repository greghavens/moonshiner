"""Exceptions raised by the VCF Operations alert collector."""


class VcfOperationsError(Exception):
    """Base class for everything this package raises."""


class ContractError(VcfOperationsError):
    """docs/contract.json is missing, malformed, or lacks an operation."""


class OperationsApiError(VcfOperationsError):
    """The appliance answered with a status the client cannot work with.

    ``status`` is the HTTP status code, ``operation_id`` the contracted
    operation that was being invoked, and ``payload`` whatever the appliance
    sent back (a decoded JSON document when it sent one, otherwise the raw
    text).
    """

    def __init__(self, message, status=None, operation_id=None, payload=None):
        super().__init__(message)
        self.status = status
        self.operation_id = operation_id
        self.payload = payload

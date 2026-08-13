"""Exception types raised by the VCF Automation policy client."""


class VcfAutomationError(Exception):
    """Base class for every error this package raises."""


class ApiError(VcfAutomationError):
    """A VCF Automation request came back with a non-success status.

    Attributes:
        method: HTTP method of the failing request.
        path: request path, without the origin.
        status: HTTP status code returned by the appliance.
        body: decoded response body, or "" when the response carried none.
    """

    def __init__(self, method, path, status, body=""):
        super().__init__("%s %s failed with HTTP %s: %s" % (method, path, status, body))
        self.method = method
        self.path = path
        self.status = status
        self.body = body


class PolicyTypeNotFoundError(ApiError):
    """The requested policy type id does not exist on the appliance."""

    def __init__(self, type_id, method, path, status, body=""):
        super().__init__(method, path, status, body)
        self.type_id = type_id

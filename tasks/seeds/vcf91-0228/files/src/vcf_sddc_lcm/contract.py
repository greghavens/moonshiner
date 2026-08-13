"""The wire contract, derived from the SDDC LCM OpenAPI specification.

Implement this against ``docs/contract.json`` -- see README.md section 2.1.
"""


class Contract:
    """Read-only view over the derived contract document."""

    @classmethod
    def load(cls, path):
        """Load the contract from ``docs/contract.json``."""
        raise NotImplementedError

    def operation(self, operation_id):
        """Return the contract entry for ``operation_id``, or raise KeyError."""
        raise NotImplementedError

    def query_parameters(self, operation_id):
        """Return the operation's query parameter wire names, in spec order."""
        raise NotImplementedError

    def build_target(self, operation_id, path_params=None, query=None):
        """Build the request target for an operation from the contract."""
        raise NotImplementedError

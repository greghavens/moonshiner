"""The wire contract, loaded from ``docs/contract.json``.

Every request target the client builds comes from here.  Nothing in this
package may hard-code a path, a query parameter name or a base path.
"""

import os

from .errors import ContractError

DEFAULT_CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docs",
    "contract.json",
)


class Operation:
    """One contracted operation.

    Attributes
    ----------
    operation_id : str
    method : str                -- ``"GET"`` / ``"POST"``
    path : str                  -- path below the contract's ``basePath``
    security : list[str]        -- security scheme names; empty means the
                                   operation is unauthenticated
    query_parameters : list[dict]
    request_body : dict | None
    response_schema : str | None
    """

    def __init__(self, operation_id, document):
        raise NotImplementedError

    @property
    def requires_authorization(self):
        """True when the operation carries a security requirement."""
        raise NotImplementedError

    def query_parameter(self, name):
        """The declared query parameter, or raise ``ContractError``."""
        raise NotImplementedError


class Contract:
    """The contract document, with the accessors the client needs."""

    def __init__(self, document):
        raise NotImplementedError

    @property
    def base_path(self):
        """The ``servers[0].url`` of the specification, e.g. ``/suite-api``."""
        raise NotImplementedError

    @property
    def security_scheme(self):
        raise NotImplementedError

    @property
    def operation_ids(self):
        raise NotImplementedError

    def operation(self, operation_id):
        raise NotImplementedError

    def schema(self, name):
        raise NotImplementedError

    def target(self, operation_id, query=None):
        """Build the request target: base path, operation path, query string.

        ``query`` is a sequence of ``(name, value)`` pairs.  Repeated names are
        preserved in the order given.  Pairs whose value is ``None`` or an
        empty string are omitted -- an unset parameter is not sent empty.
        """
        raise NotImplementedError


def load_contract(path=None):
    """Load ``docs/contract.json`` (or ``path``) into a :class:`Contract`."""
    raise NotImplementedError

"""A client for the VCF Operations alert collection.

Reads the whole paginated collection and hands it back in a stable order.
Standard library only.
"""

from .errors import OperationsApiError  # noqa: F401  (re-exported for callers)

DEFAULT_PAGE_SIZE = 1000


class OperationsClient:
    """Talks to one VCF Operations appliance over the contracted operations.

    Parameters
    ----------
    base_url : str
        The appliance root, e.g. ``https://vcfops.example.net``.  The
        ``/suite-api`` prefix belongs to the contract, not to this value.
    username, password : str
    auth_source : str | None
        Optional; unset means the local user directory.
    contract : Contract | None
        Defaults to the contract under ``docs/``.
    timeout : float
    """

    def __init__(self, base_url, username, password, auth_source=None,
                 contract=None, timeout=30.0):
        raise NotImplementedError

    def __enter__(self):
        raise NotImplementedError

    def __exit__(self, *exc_info):
        raise NotImplementedError

    @property
    def token(self):
        """The token held by this client, or ``None``."""
        raise NotImplementedError

    def acquire_token(self):
        """Acquire a session token and remember it.  Returns the token."""
        raise NotImplementedError

    def release_token(self):
        """Release the session token.  A no-op when none is held."""
        raise NotImplementedError

    def fetch_alerts(self, page_size=DEFAULT_PAGE_SIZE, resource_ids=None,
                     alert_ids=None):
        """Read the whole alert collection and return it in stable order."""
        raise NotImplementedError


def collect_alerts(base_url, username, password, auth_source=None,
                   page_size=DEFAULT_PAGE_SIZE, resource_ids=None,
                   alert_ids=None, contract=None, timeout=30.0):
    """Acquire a token, read the collection, release the token."""
    raise NotImplementedError

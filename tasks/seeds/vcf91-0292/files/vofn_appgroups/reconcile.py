"""Retry-safe application-group reconciler for VCF Operations for Networks 9.1.

The wire contract is pinned in ``docs/contract.json``, a focused projection of
the VCF Operations for Networks OpenAPI specification.  Four operations are in
scope:

* ``create``                        -- ``POST /api/ni/auth/token``
* ``delete``                        -- ``DELETE /api/ni/auth/token``
* ``getSavedApplicationsSummaries`` -- ``GET /api/ni/groups/applications/fetch``
* ``addApplication``                -- ``POST /api/ni/groups/applications``

``addApplication`` is the mutating operation.  The pinned specification offers
no idempotency key and no conditional-request header for it, so the only
retry-safe construction is to enumerate the saved application summaries before
creating and to re-enumerate after any create attempt that did not return a
readable ``201``.

This module is standard-library only.
"""

from dataclasses import dataclass

BASE_PATH = "/api/ni"

CREATE_TOKEN_OPERATION_ID = "create"
DELETE_TOKEN_OPERATION_ID = "delete"
LIST_SUMMARIES_OPERATION_ID = "getSavedApplicationsSummaries"
ADD_APPLICATION_OPERATION_ID = "addApplication"

TOKEN_PATH = BASE_PATH + "/auth/token"
LIST_SUMMARIES_PATH = BASE_PATH + "/groups/applications/fetch"
ADD_APPLICATION_PATH = BASE_PATH + "/groups/applications"

#: ``entity_type`` discriminator value carried by an application summary.
APPLICATION_ENTITY_TYPE = "Application"

#: Largest page this client is willing to request from the summaries operation.
MAX_PAGE_SIZE = 1000

#: Largest number of create attempts a single reconcile call may make.
MAX_ATTEMPTS = 5

#: Statuses that mean the appliance may or may not have applied the mutation.
RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})


class AppGroupError(Exception):
    """Base class for every error raised by this module."""


class APIError(AppGroupError):
    """A contract operation answered with a non-success HTTP status."""

    def __init__(self, operation_id, status_code, code=None, message=None):
        super().__init__(
            "%s failed with HTTP status %d" % (operation_id, status_code)
        )
        self.operation_id = operation_id
        self.status_code = status_code
        self.code = code
        self.message = message


class ProtocolError(AppGroupError):
    """A response could not be reconciled with the pinned contract."""

    def __init__(self, operation_id, reason):
        super().__init__("%s returned an unusable response: %s" % (operation_id, reason))
        self.operation_id = operation_id
        self.reason = reason


class TransportError(AppGroupError):
    """The request never produced an HTTP response.

    The message must never disclose the password, the auth token, or the
    underlying transport error text.
    """

    def __init__(self, operation_id):
        super().__init__("%s did not produce an HTTP response" % (operation_id,))
        self.operation_id = operation_id


@dataclass(frozen=True)
class EnsureOutcome:
    """Result of reconciling one application group.

    ``created`` is true only when this call itself read a ``201`` from
    ``addApplication``.  A create attempt whose response was lost still leaves
    ``created`` false, because the client never observed the creation.
    """

    entity_id: str
    name: str
    created: bool
    create_attempts: int
    pages_read: int


class ApplicationGroupClient:
    """Reconciles application groups on one appliance.

    ``base_url`` is the appliance root, for example ``https://vofn.example.com``.
    The credential fields feed the ``create`` request body; ``domain_type`` is
    ``None``, ``"LOCAL"`` or ``"LDAP"``.
    """

    def __init__(
        self,
        base_url,
        username,
        password,
        *,
        domain_type=None,
        domain_value=None,
        timeout=30.0,
    ):
        raise NotImplementedError

    def __enter__(self):
        raise NotImplementedError

    def __exit__(self, exc_type, exc, tb):
        raise NotImplementedError

    def ensure_application(self, name, *, page_size=100, max_attempts=3) -> EnsureOutcome:
        """Make sure exactly one saved application is named ``name``."""
        raise NotImplementedError

    def close(self) -> None:
        """Release the auth token this client obtained, at most once."""
        raise NotImplementedError


def render_outcome(outcome: EnsureOutcome) -> str:
    """Render ``outcome`` as the canonical reconciliation document."""
    raise NotImplementedError

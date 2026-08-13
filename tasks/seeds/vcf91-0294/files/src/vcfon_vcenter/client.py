"""REST client for the four VCF Operations for Networks operations this tool uses.

Every method here maps to exactly one operationId in docs/contract.json. The
contract is the authority for method, path, request payload and success status;
docs/official_sources.json records where the contract came from.

The three body builders are pure functions of the plan objects. They are what
decides which optional fields reach the wire, so they are worth getting right on
their own, before any socket is involved.

Standard library only.
"""

from __future__ import annotations

from .errors import VcfOnApiError
from .transport import send


def user_credential_body(credentials, domain=None):
    """Build the UserCredential body for operationId ``create``.

    ``domain`` is an optional :class:`~vcfon_vcenter.plan.Domain`.
    """
    raise NotImplementedError


def validation_body(spec):
    """Build the VCenterDataSourceValidationRequest body for ``validateVCenter``.

    ``spec`` is a :class:`~vcfon_vcenter.plan.VcenterSpec`. Only the properties
    the validation schema actually declares may appear.
    """
    raise NotImplementedError


def datasource_body(spec):
    """Build the VCenterDataSourceRequest body for ``addVcenterDatasource``.

    ``spec`` is a :class:`~vcfon_vcenter.plan.VcenterSpec`.
    """
    raise NotImplementedError


class VcenterDataSourceClient:
    """Talks to one appliance.

    ``base_url`` is the appliance origin, e.g. ``http://127.0.0.1:8443``. The
    server base path from the contract is appended by this client, not by the
    caller.
    """

    def __init__(self, base_url, timeout=10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None

    # -- operationId: create -------------------------------------------------
    def create_auth_token(self, credentials, domain=None):
        """POST /api/ni/auth/token, store and return the issued token string.

        Carries no Authorization header. Raises :class:`VcfOnApiError` when the
        appliance answers anything other than 200.
        """
        raise NotImplementedError

    # -- operationId: delete -------------------------------------------------
    def delete_auth_token(self):
        """DELETE /api/ni/auth/token, then forget the token.

        Sends no request body. Raises :class:`VcfOnApiError` when the appliance
        answers anything other than 204.
        """
        raise NotImplementedError

    # -- operationId: validateVCenter ----------------------------------------
    def validate_vcenter(self, spec):
        """POST /api/ni/data-sources/vcenters/validate.

        Returns the decoded BaseDataSourceValidationResponse body, which is a
        dict with ``code`` and ``message``. Raises :class:`VcfOnApiError` when
        the appliance answers anything other than 200; a 200 whose body ``code``
        is not 200 is a returned value, not an exception.
        """
        raise NotImplementedError

    # -- operationId: addVcenterDatasource -----------------------------------
    def add_vcenter_datasource(self, spec):
        """POST /api/ni/data-sources/vcenters.

        Returns the decoded VCenterDataSource body. Raises
        :class:`VcfOnApiError` when the appliance answers anything other
        than 201.
        """
        raise NotImplementedError

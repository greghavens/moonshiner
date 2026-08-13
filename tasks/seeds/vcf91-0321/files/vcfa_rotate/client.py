"""HTTP client for the operations named in docs/contract.json.

Standard library only (urllib, json, ...). Nothing here may import a third-party
package.

Every method below is a stub. Fill them in so that each one puts exactly the request
on the wire that docs/contract.json describes -- in particular honour
``wireRules.omitUnsetOptionalBodyFields`` and
``wireRules.omitUnsetOptionalQueryParameters``: an option the caller did not pass must
leave no trace in the request at all, and an option the caller *did* pass must survive
even when its value is falsy.
"""

from __future__ import annotations


class VcfaApiError(Exception):
    """Raised for a 4xx/5xx response.

    Attributes:
        status: the HTTP status code.
        minor_error_code: the ``minorErrorCode`` from the error body, or None.
        message: the ``message`` from the error body, or the raw body.
        body: the parsed error body, or None if it was not JSON.
    """

    def __init__(self, status, minor_error_code, message, body=None):
        super().__init__("%s: %s" % (status, message))
        self.status = status
        self.minor_error_code = minor_error_code
        self.message = message
        self.body = body


class VcfaClient:
    """Talks to one VCF Automation endpoint.

    Args:
        base_url: scheme://host:port of the appliance, no trailing path.
        token: the bearer JWT.
        timeout: socket timeout in seconds.
    """

    def __init__(self, base_url, token, timeout=30.0):
        raise NotImplementedError

    # -- namedCredentials ---------------------------------------------------------

    def query_named_credentials(
        self, filter_expr=None, sort_asc=None, sort_desc=None, page=1, page_size=25
    ):
        """GET /cloudapi/1.0.0/namedCredentials -> the paged response dict."""
        raise NotImplementedError

    def get_named_credential(self, credential_id):
        """GET /cloudapi/1.0.0/namedCredentials/{id} -> the NamedCredential dict."""
        raise NotImplementedError

    def create_named_credential(self, name, username, password, entity=None):
        """POST /cloudapi/1.0.0/namedCredentials -> the created NamedCredential dict."""
        raise NotImplementedError

    def update_named_credential(
        self, credential_id, name=None, username=None, password=None, entity=None
    ):
        """PUT /cloudapi/1.0.0/namedCredentials/{id} -> the updated NamedCredential dict."""
        raise NotImplementedError

    def delete_named_credential(self, credential_id):
        """DELETE /cloudapi/1.0.0/namedCredentials/{id}.

        The response is 202 with an empty body; return the tracking task URI carried in
        the Location header.
        """
        raise NotImplementedError

    # -- testConnection -----------------------------------------------------------

    def test_connection(
        self,
        host,
        port,
        secure=None,
        timeout=None,
        hostname_verification_algorithm=None,
        additional_ca_issuers=None,
        proxy_connection=None,
        pre_configured_proxy=None,
    ):
        """POST /cloudapi/1.0.0/testConnection -> the ConnectionProbeResult dict."""
        raise NotImplementedError

    # -- virtualCenters -----------------------------------------------------------

    def get_virtual_center(self, vc_urn):
        """GET /cloudapi/1.0.0/virtualCenters/{vcUrn} -> the VCenterServer dict."""
        raise NotImplementedError

    def update_virtual_center(self, vc_urn, body):
        """PUT /cloudapi/1.0.0/virtualCenters/{vcUrn}.

        ``body`` is the VCenterServer payload to send, already assembled by the caller.
        The response is 202 with an empty body; return the tracking task URI carried in
        the Location header.
        """
        raise NotImplementedError

    # -- auditTrail ---------------------------------------------------------------

    def query_audit_trail(
        self, filter_expr=None, sort_asc=None, sort_desc=None, page=1, page_size=25
    ):
        """GET /cloudapi/1.0.0/auditTrail -> the paged response dict."""
        raise NotImplementedError

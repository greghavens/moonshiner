"""REST client for the six VCF Operations for Networks operations this tool uses.

Every method here maps to exactly one operationId in docs/contract.json. The
contract is the authority for method, path, request payload and success status;
docs/official_sources.json records where the contract came from.

Standard library only.
"""

from __future__ import annotations

from .errors import VcfOnApiError
from .transport import send


class SyslogSettingsClient:
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
    def create_auth_token(self, credentials):
        """POST /api/ni/auth/token, store and return the issued token.

        Carries no Authorization header. Returns the token string.
        """
        raise NotImplementedError

    # -- operationId: delete -------------------------------------------------
    def delete_auth_token(self):
        """DELETE /api/ni/auth/token, then forget the token."""
        raise NotImplementedError

    # -- operationId: getSyslogTargetList ------------------------------------
    def list_syslog_targets(self):
        """GET /api/ni/settings/syslog.

        Returns the list of SyslogTarget objects from the response's ``data``
        array, in the order the appliance returned them.
        """
        raise NotImplementedError

    # -- operationId: addSyslogTarget ----------------------------------------
    def add_syslog_target(self, target):
        """POST /api/ni/settings/syslog for a SyslogTargetSpec."""
        raise NotImplementedError

    # -- operationId: updateSyslogTarget -------------------------------------
    def update_syslog_target(self, target):
        """PUT /api/ni/settings/syslog/{ip-or-fqdn} for a SyslogTargetSpec."""
        raise NotImplementedError

    # -- operationId: sendSyslogTestMessage ----------------------------------
    def send_syslog_test_message(self, target):
        """POST /api/ni/settings/syslog/send-test-log for a SyslogTargetSpec.

        Returns the decoded StatusResponse body.
        """
        raise NotImplementedError

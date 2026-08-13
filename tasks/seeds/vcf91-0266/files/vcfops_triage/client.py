"""HTTP client for the focused VCF Operations suite-api contract.

Every operation implemented here is named by ``docs/contract.json``; no other
route may be contacted. Requests must be issued through the module-level
``urlopen`` imported below.
"""

import json
from typing import Any, Dict, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .models import AlertQuery, Credentials, OperationsError

#: ``servers[0].url`` of the pinned specification. Every request target starts
#: with this base path.
BASE_PATH = "/suite-api"


class OperationsClient:
    """Client for the eight operations named by the focused contract.

    Parameters
    ----------
    base_url:
        Origin of the VCF Operations appliance, for example
        ``http://127.0.0.1:8443``. It carries no path of its own; the client
        appends :data:`BASE_PATH` and the operation path.
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        if not base_url or base_url.endswith("/"):
            raise ValueError("base_url must be an origin without a trailing slash")
        self.base_url = base_url
        self.timeout = timeout
        self._token: Optional[str] = None

    @property
    def token(self) -> Optional[str]:
        """The token issued by :meth:`acquire_token`, or ``None``."""

        return self._token

    # -- Auth ---------------------------------------------------------------

    def acquire_token(self, credentials: Credentials) -> str:
        """``acquireToken``: exchange credentials for a suite-api token.

        Stores the token on the client and returns it.
        """

        raise NotImplementedError

    def release_token(self) -> None:
        """``releaseToken``: terminate the current session and forget the token."""

        raise NotImplementedError

    # -- Identity provider LDAP directories ---------------------------------

    def get_ldap_directories(self, idp_config_id: str) -> List[Dict[str, Any]]:
        """``getLdapDirectories``: LDAP directories of one identity provider."""

        raise NotImplementedError

    def get_ldap_sync_logs(
        self,
        idp_config_id: str,
        ldap_directory_id: str,
        *,
        page: int,
        page_size: int,
    ) -> Dict[str, Any]:
        """``getLdapSyncLogs``: one page of synchronization execution logs.

        Returns the decoded ``LdapSyncLogs`` object, including ``pageInfo``.
        The optional ``last`` parameter is not used by this task.
        """

        raise NotImplementedError

    def get_ldap_sync_log(
        self,
        idp_config_id: str,
        ldap_directory_id: str,
        sync_log_id: str,
    ) -> Dict[str, Any]:
        """``getLdapSyncLogById``: full detail of one synchronization run."""

        raise NotImplementedError

    # -- Alerts and symptoms ------------------------------------------------

    def query_alerts(self, query: AlertQuery) -> List[Dict[str, Any]]:
        """``queryAlert``: alerts matching the query body.

        The optional ``page`` and ``pageSize`` query parameters are not used by
        this task.
        """

        raise NotImplementedError

    def get_alert_contributing_symptoms(
        self,
        alert_ids: Sequence[str],
    ) -> List[Dict[str, Any]]:
        """``getAlertContributingSymptoms``: triggered symptoms per alert.

        All identifiers are looked up in a single request. Returns the decoded
        ``alert-contributing-symptom`` entries.
        """

        raise NotImplementedError

    def get_symptoms(
        self,
        resource_id: str,
        *,
        active_only: bool,
        include_alarm_info: bool,
    ) -> List[Dict[str, Any]]:
        """``getSymptoms``: symptoms of one resource.

        The optional ``page`` and ``pageSize`` query parameters are not used by
        this task.
        """

        raise NotImplementedError

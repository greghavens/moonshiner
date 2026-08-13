"""Correlate LDAP synchronization logs with active alerts and symptoms."""

from .client import OperationsClient
from .models import (
    AlertQuery,
    Credentials,
    Diagnosis,
    OperationsError,
    SymptomEvidence,
    SyncFailure,
)

#: Criticality filter for the ``queryAlert`` body.
ALERT_CRITICALITY = ("CRITICAL", "IMMEDIATE")

#: Page size used when walking ``getLdapSyncLogs``.
SYNC_LOG_PAGE_SIZE = 5


def diagnose(
    client: OperationsClient,
    *,
    credentials: Credentials,
    idp_config_id: str,
) -> Diagnosis:
    """Run the documented triage and return the correlated :class:`Diagnosis`.

    See ``README.md`` for the procedure, the selection rules, and the order in
    which the contract operations are invoked.
    """

    raise NotImplementedError

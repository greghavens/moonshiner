"""Standard-library-only application-group reconciler for VCF Operations for Networks 9.1.

The package exposes a single focused workflow: make sure a named application
group exists on a VCF Operations for Networks appliance, in a way that is safe
to run again after a failure without creating the group twice.  Only the
operations named in ``docs/contract.json`` are used.
"""

from .reconcile import (
    ADD_APPLICATION_OPERATION_ID,
    APPLICATION_ENTITY_TYPE,
    BASE_PATH,
    DELETE_TOKEN_OPERATION_ID,
    CREATE_TOKEN_OPERATION_ID,
    LIST_SUMMARIES_OPERATION_ID,
    MAX_ATTEMPTS,
    MAX_PAGE_SIZE,
    RETRYABLE_STATUSES,
    APIError,
    AppGroupError,
    ApplicationGroupClient,
    EnsureOutcome,
    ProtocolError,
    TransportError,
    render_outcome,
)

__all__ = [
    "ADD_APPLICATION_OPERATION_ID",
    "APPLICATION_ENTITY_TYPE",
    "BASE_PATH",
    "CREATE_TOKEN_OPERATION_ID",
    "DELETE_TOKEN_OPERATION_ID",
    "LIST_SUMMARIES_OPERATION_ID",
    "MAX_ATTEMPTS",
    "MAX_PAGE_SIZE",
    "RETRYABLE_STATUSES",
    "APIError",
    "AppGroupError",
    "ApplicationGroupClient",
    "EnsureOutcome",
    "ProtocolError",
    "TransportError",
    "render_outcome",
]

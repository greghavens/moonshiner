"""Drain-safe SDDC Manager credential rotation.

Standard library only. The REST surface is pinned by docs/contract.json, whose
provenance is recorded in docs/official_sources.json.
"""

from .broker import (
    CredentialBroker,
    Lease,
    RotationFailed,
    RotationResult,
    RotationTimeout,
)
from .client import AuthenticationError, SddcApiError, SddcManagerClient
from .contract import Contract, ContractError
from .spec import (
    OPERATION_REMEDIATE,
    OPERATION_ROTATE,
    OPERATION_UPDATE,
    TargetCredential,
    build_token_spec,
    build_update_spec,
)

__all__ = [
    "AuthenticationError",
    "Contract",
    "ContractError",
    "CredentialBroker",
    "Lease",
    "OPERATION_REMEDIATE",
    "OPERATION_ROTATE",
    "OPERATION_UPDATE",
    "RotationFailed",
    "RotationResult",
    "RotationTimeout",
    "SddcApiError",
    "SddcManagerClient",
    "TargetCredential",
    "build_token_spec",
    "build_update_spec",
]

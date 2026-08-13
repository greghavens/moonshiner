"""Credential rotation for VCF Automation Provider Infrastructure APIs.

Standard library only. See README.md for the task and docs/contract.json for the
API contract this package must speak.
"""

from .client import VcfaApiError, VcfaClient
from .rotation import rotate_named_credential

__all__ = ["VcfaApiError", "VcfaClient", "rotate_named_credential"]

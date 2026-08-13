"""Minimal, standard-library-only client for the VCF Automation Policies API.

Targets VCF Automation in VMware Cloud Foundation 9.1. The wire contract lives
in docs/contract.json and was transcribed from the Broadcom xAPIs reference
pages listed in docs/official_sources.json.
"""

from .client import PolicyClient
from .errors import ApiError, PolicyTypeNotFoundError, VcfAutomationError

__all__ = [
    "PolicyClient",
    "ApiError",
    "PolicyTypeNotFoundError",
    "VcfAutomationError",
]

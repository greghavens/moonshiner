"""A small, dependency-free client for the VCF Automation (VCF 9.1) deployment APIs.

Everything this package sends must conform to ``docs/contract.json``, which was derived
from the VCF Automation xAPIs reference documentation. The Python standard library is
the only dependency.
"""

__version__ = "0.1.0"

from .errors import VcfaApiError, VcfaError
from .transport import Response, request

__all__ = ["Response", "VcfaApiError", "VcfaError", "request"]

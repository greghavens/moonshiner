"""Standard-library-only client for the VCF 9.1 SDDC LCM task operations."""

__all__ = ["Contract", "SddcLcmClient"]

from .client import SddcLcmClient
from .contract import Contract

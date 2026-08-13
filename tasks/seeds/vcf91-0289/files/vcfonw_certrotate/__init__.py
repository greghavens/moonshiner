"""Certificate rotation client for VCF Operations for Networks 9.1.

The REST surface this package speaks is pinned in ``docs/contract.json``, which
is derived from ``specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml``
in the vmware/vcf-api-specs repository. Only the four operations named there may
be called.

Standard library only: this package must not import any third-party module.
"""

from .client import CertificateRotationClient
from .model import ApiError, PollTimeoutError, RotationOutcome

__all__ = [
    "ApiError",
    "CertificateRotationClient",
    "PollTimeoutError",
    "RotationOutcome",
]

__version__ = "9.1.0"

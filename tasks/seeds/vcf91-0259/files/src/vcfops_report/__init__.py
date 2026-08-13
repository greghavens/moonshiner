"""Client for the VCF Operations report-generation workflow (VCF 9.1).

Public surface:

    from vcfops_report import VcfOperationsClient, ReportResult
    from vcfops_report.errors import (
        VcfOperationsError, ApiError, AuthenticationError,
        ReportGenerationFailed, ReportTimeout,
    )
"""

from __future__ import annotations

from .client import VcfOperationsClient
from .errors import (
    ApiError,
    AuthenticationError,
    ReportGenerationFailed,
    ReportTimeout,
    VcfOperationsError,
)
from .models import ReportResult

__all__ = [
    "VcfOperationsClient",
    "ReportResult",
    "VcfOperationsError",
    "ApiError",
    "AuthenticationError",
    "ReportGenerationFailed",
    "ReportTimeout",
]

__version__ = "0.1.0"

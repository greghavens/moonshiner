"""Stdlib-only client for diagnosing failed SDDC Manager tasks (VCF 9.0).

The package exposes a single entry point:

    diagnose_failure(base_url, username, password) -> dict
"""

from .diagnose import diagnose_failure

__all__ = ["diagnose_failure"]

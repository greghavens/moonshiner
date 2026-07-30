"""Small stdlib-only client for the focused VCF 9.1 contract."""

from .client import VCenterAPIError, VCenterClient, collect_diagnosis

__all__ = ["VCenterAPIError", "VCenterClient", "collect_diagnosis"]

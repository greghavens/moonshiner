"""Small, standard-library-only VCF SDDC Manager client."""

from .client import SddcManagerClient, SddcManagerError

__all__ = ["SddcManagerClient", "SddcManagerError"]

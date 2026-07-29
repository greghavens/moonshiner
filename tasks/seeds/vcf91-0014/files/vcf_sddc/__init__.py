"""Small standard-library client for the task-scoped SDDC Manager contract."""

from .client import SddcManagerClient, SddcManagerError

__all__ = ["SddcManagerClient", "SddcManagerError"]

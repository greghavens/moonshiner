"""Small standard-library client for a VCF Operations for Logs API subset."""

from .batch import collect_event_queries
from .client import LogsApiError, LogsClient

__all__ = ["LogsApiError", "LogsClient", "collect_event_queries"]

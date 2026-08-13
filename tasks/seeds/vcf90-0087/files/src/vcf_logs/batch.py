"""Batch helpers for VCF Operations for Logs queries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .client import LogsClient


def collect_event_queries(
    client: LogsClient, queries: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Return one decoded response per query mapping, in input order."""
    raise NotImplementedError("collect event query responses")

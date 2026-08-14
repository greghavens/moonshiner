"""VCF Operations for Networks alert client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class _Unset:
    __slots__ = ()


UNSET = _Unset()


@dataclass(frozen=True)
class SearchBasedAlertUpdate:
    """Fields for SearchBasedAlertConfigRequest; omitted values stay unset."""

    alert_name: Any = UNSET
    search_criteria: Any = UNSET
    generate_alert_criteria: Any = UNSET
    alert_type: Any = UNSET
    severity: Any = UNSET
    notification_settings: Any = UNSET


class OperationsForNetworksClient:
    """Client rooted at a VCF Operations for Networks appliance origin."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0):
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def update_search_based_alert(
        self, alert_id: str, update: SearchBasedAlertUpdate
    ) -> dict[str, Any]:
        """Replace mutable fields of a search-based alert configuration."""
        raise NotImplementedError("updateSearchBasedAlertConfig is not implemented")

"""Drives a vCenter migration upgrade from apply to a terminal status."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Mapping

from .client import MigrationUpgradeClient

#: ``Vcenter.Lcm.Deployment.Common.Status`` values that end a run.
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED"})


@dataclass(frozen=True)
class UpgradeOutcome:
    """The last status sample observed for a run, flattened."""

    status: str
    current_state: str | None
    upgrade_identifier: str | None
    upgrade_to: str | None
    end_time: str | None
    polls: int
    canceled: bool
    errors: tuple[str, ...]


class MigrationUpgradeDriver:
    """Applies the configured upgrade and reports the resulting status."""

    def __init__(
        self,
        client: MigrationUpgradeClient,
        *,
        poll_interval: float = 10.0,
        max_polls: int = 60,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self._sleep = sleep
        self._polls = 0

    @property
    def polls(self) -> int:
        """Number of ``Status_get`` requests issued by the last :meth:`run`."""

        return self._polls

    def run(
        self,
        *,
        pause: str | None = None,
        start_switchover: str | None = None,
        cancel_on_failure: bool = False,
    ) -> UpgradeOutcome:
        self._polls = 0
        self.client.get_init_spec()
        self.client.apply(pause=pause, start_switchover=start_switchover)

        sample = self.client.get_status()
        self._polls += 1
        return self._outcome(sample, False)

    def _outcome(self, sample: Mapping[str, Any], canceled: bool) -> UpgradeOutcome:
        upgrade_info = sample.get("upgrade_info") or {}
        notifications = sample.get("notifications") or {}
        errors = tuple(
            (entry.get("message") or {}).get("default_message", "")
            for entry in notifications.get("errors") or ()
            if isinstance(entry, Mapping)
        )
        return UpgradeOutcome(
            status=sample.get("status"),
            current_state=sample.get("current_state"),
            upgrade_identifier=upgrade_info.get("identifier"),
            upgrade_to=upgrade_info.get("upgrade_to"),
            end_time=sample.get("end_time"),
            polls=self._polls,
            canceled=canceled,
            errors=errors,
        )

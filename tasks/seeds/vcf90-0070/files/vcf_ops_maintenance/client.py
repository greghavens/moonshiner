"""VCF Operations maintenance-schedule workflow.

Only this file is editable for the exercise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    hour: int
    minute_of_the_hour: int
    duration: int
    schedule_type: str
    recurrence: int | None = None
    day_of_the_month: int | None = None
    days_of_the_month: tuple[str, ...] | None = None
    weeks_of_the_month: tuple[str, ...] | None = None
    days_of_the_week: tuple[str, ...] | None = None
    month: int | None = None
    months: tuple[int, ...] | None = None
    start_date: str | None = None
    expiration_date: str | None = None
    time_zone: str | None = None
    expire_runs: int | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceSchedule:
    key: str
    schedule: ScheduleSpec
    id: str | None = None


@dataclass(frozen=True, slots=True)
class StepResult:
    operation_id: str
    status_code: int
    schedule_id: str | None


@dataclass(frozen=True, slots=True)
class ChangeReport:
    completed: tuple[StepResult, ...]


class ApiError(Exception):
    """Base error for HTTP and transport failures."""


class ResponseContractError(ApiError):
    """A success response did not match the focused OpenAPI contract."""


class ChangeFailed(ApiError):
    """A workflow step failed after zero or more confirmed steps."""

    def __init__(
        self,
        message: str,
        *,
        failed_operation_id: str,
        status_code: int | None,
        report: ChangeReport,
    ) -> None:
        super().__init__(message)
        self.failed_operation_id = failed_operation_id
        self.status_code = status_code
        self.report = report


class VcfOperationsClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        raise NotImplementedError

    def apply_change(
        self,
        create: MaintenanceSchedule,
        update: MaintenanceSchedule,
        retire_ids: Sequence[str],
    ) -> ChangeReport:
        raise NotImplementedError

"""Implement the contract-pinned vCenter resize reporting client here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepResult:
    name: str
    operation_id: str
    state: str
    http_status: int
    error_type: str | None
    message: str | None


@dataclass(frozen=True)
class ResizeReport:
    vm: str
    overall_state: str
    completed_step_count: int
    failed_operation_id: str | None
    steps: tuple[StepResult, ...]


class ProtocolError(RuntimeError):
    """Transport or response data violated the focused contract."""


class ResizeClient:
    """TODO: implement the public client described in task.json."""

    def __init__(
        self,
        base_url: str,
        session_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def resize_and_start(
        self,
        vm: str,
        cpu_count: int,
        memory_mib: int,
    ) -> ResizeReport:
        raise NotImplementedError

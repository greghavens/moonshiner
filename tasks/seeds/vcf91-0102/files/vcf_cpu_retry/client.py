"""Implement the contract-pinned vCenter CPU update client here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CpuUpdateResult:
    vm: str
    count: int
    attempts: int
    operation_id: str


class VcenterError(RuntimeError):
    """An HTTP failure returned by vCenter."""


class ProtocolError(RuntimeError):
    """A successful response violated the focused contract."""


class RetryExhaustedError(RuntimeError):
    """Both attempts ended before an HTTP response was available."""


class CpuUpdateClient:
    """TODO: implement the public client described in task.json."""

    def __init__(
        self,
        base_url: str,
        session_token: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        raise NotImplementedError

    def set_cpu_count(self, vm: str, count: int) -> CpuUpdateResult:
        raise NotImplementedError

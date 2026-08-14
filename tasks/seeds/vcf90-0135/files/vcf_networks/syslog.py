"""VCF Operations for Networks syslog change client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SyslogTarget:
    """A syslog target accepted by VCF Operations for Networks."""

    ip_or_fqdn: str
    port: int
    protocol: str
    nick_name: str | None = None
    collector_id: str | None = None


@dataclass(frozen=True, slots=True)
class SyslogMapping:
    """One source-to-syslog mapping."""

    syslog_source: str
    syslog_ip: str
    collector_id: str | None = None


@dataclass(frozen=True, slots=True)
class StepResult:
    """The observed result of one attempted OpenAPI operation."""

    operation_id: str
    success: bool
    status_code: int | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ChangeReport:
    """Ordered results for a requested syslog change."""

    steps: tuple[StepResult, ...]

    @property
    def success(self) -> bool:
        return bool(self.steps) and all(step.success for step in self.steps)


class SyslogClient:
    """Client for the three-operation syslog change described in the contract."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout

    def apply_change(
        self,
        target: SyslogTarget,
        mappings: list[SyslogMapping],
        *,
        enabled: bool,
    ) -> ChangeReport:
        """Apply the target, mapping, and status operations in order."""

        raise NotImplementedError


"""Exceptions raised by :mod:`vcflcm`."""

from __future__ import annotations

from typing import Sequence


class VcfLcmError(Exception):
    """Base class for every error raised by this package."""


class ApiError(VcfLcmError):
    """A vCenter response that was not the success status named by the contract.

    ``messages`` holds the ``default_message`` of each entry in the
    ``Vapi.Std.Errors.Error`` envelope, outermost first.
    """

    def __init__(
        self,
        status: int,
        operation_id: str,
        error_type: str | None = None,
        messages: Sequence[str] = (),
    ) -> None:
        self.status = status
        self.operation_id = operation_id
        self.error_type = error_type
        self.messages = tuple(messages)
        detail = self.messages[0] if self.messages else (error_type or "no detail")
        super().__init__(f"{operation_id} returned HTTP {status}: {detail}")


class InvalidApplySpec(VcfLcmError, ValueError):
    """The requested ApplySpec is rejected by the contract before any request."""


class UpgradeNotConfigured(VcfLcmError):
    """No migration upgrade is configured on the appliance."""


class UpgradePollTimeout(VcfLcmError):
    """The upgrade did not reach a terminal status within the poll budget."""

    def __init__(self, polls: int, last_status: str | None = None) -> None:
        self.polls = polls
        self.last_status = last_status
        super().__init__(
            f"upgrade did not reach a terminal status after {polls} status polls"
            + (f" (last status {last_status})" if last_status else "")
        )

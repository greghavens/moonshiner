"""Result and error types for the certificate rotation flow.

These shapes are fixed; the rotation logic in :mod:`vcfonw_certrotate.client`
is what has to be written.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


class ApiError(Exception):
    """Raised when the appliance answers an operation with a non-success status.

    ``status`` is the HTTP status that came back, ``body`` is the decoded JSON
    error payload when the response carried one (the spec models these as
    ``ApiError`` with ``code``/``message``/``details``), otherwise ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        url: Optional[str] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body


class PollTimeoutError(Exception):
    """Raised when the certificate update has not reached a terminal state in time.

    ``update_id`` is the certificate update id that was being polled,
    ``last_status`` the most recent non-terminal status observed, and
    ``poll_count`` how many status reads were issued before giving up.
    """

    def __init__(
        self,
        message: str,
        *,
        update_id: Optional[str] = None,
        last_status: Optional[str] = None,
        poll_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.update_id = update_id
        self.last_status = last_status
        self.poll_count = poll_count


@dataclass
class RotationOutcome:
    """Terminal result of one certificate rotation.

    ``status`` is always a terminal value from the contract's status model
    (``SUCCESS`` or ``FAILED``); a rotation that ended in ``FAILED`` is returned,
    not raised.
    """

    update_id: str
    status: str
    poll_count: int
    error_message: Optional[str] = None
    updated_nodes: list = field(default_factory=list)
    failed_nodes: list = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCESS"

"""Exception hierarchy for the SDDC Manager bundle-download client."""

from __future__ import annotations


class SddcManagerError(Exception):
    """Base class for every error raised by this package."""


class ApiError(SddcManagerError):
    """A non-success HTTP response from SDDC Manager.

    ``error`` holds the decoded ``Error`` body when the response carried one.
    """

    def __init__(self, message, status_code=None, error=None):
        super().__init__(message)
        self.status_code = status_code
        self.error = error or {}


class AuthenticationError(ApiError):
    """POST /v1/tokens failed, or a request was rejected as unauthenticated."""


class TaskFailedError(SddcManagerError):
    """The polled task reached a terminal state that is not a success."""

    def __init__(self, message, task_id=None, status=None, errors=None, task=None):
        super().__init__(message)
        self.task_id = task_id
        self.status = status
        self.errors = errors or []
        self.task = task or {}


class TaskTimeoutError(SddcManagerError):
    """The task did not reach a terminal state before the poll timeout."""

    def __init__(self, message, task_id=None, last_status=None, polls=None):
        super().__init__(message)
        self.task_id = task_id
        self.last_status = last_status
        self.polls = polls

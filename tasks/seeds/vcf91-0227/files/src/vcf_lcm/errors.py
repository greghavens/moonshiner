"""Exceptions raised by :mod:`vcf_lcm`.

These are provided complete -- do not change their names or constructor
signatures, the verification suite catches them by type and reads their
attributes.
"""


class LcmApiError(RuntimeError):
    """A non-2xx response, or a response the workflow cannot use.

    ``status_code`` is the HTTP status when one was received, else ``None``.
    ``payload`` is the decoded ``ErrorResponse`` body when the body was JSON.
    """

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class TokenRefreshError(LcmApiError):
    """A request was rejected as unauthorized even after the token was refreshed."""


class TaskFailedError(RuntimeError):
    """A polled task reached a terminal status that is not a success.

    ``task`` is the terminal task object as returned by the service.
    """

    def __init__(self, message, task=None):
        super().__init__(message)
        self.task = task


class TaskTimeoutError(RuntimeError):
    """A polled task did not reach a terminal status within the timeout.

    ``task`` is the last task object observed, if any.
    """

    def __init__(self, message, task=None):
        super().__init__(message)
        self.task = task


__all__ = [
    "LcmApiError",
    "TaskFailedError",
    "TaskTimeoutError",
    "TokenRefreshError",
]

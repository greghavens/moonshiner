"""Exception hierarchy for the VCF Operations report client.

These types are part of the package's published surface: callers and the test
suite catch them by name. They are already implemented -- raise them from
client.py, do not redefine them.
"""

from __future__ import annotations


class VcfOperationsError(Exception):
    """Base class for every error this package raises."""


class ApiError(VcfOperationsError):
    """A VCF Operations endpoint answered with a non-success HTTP status.

    Attributes:
        status: the HTTP status code.
        operation_id: the contract operationId that was being invoked.
        message: the server's message, if it sent a JSON body with one.
        body: the raw response body as text.
    """

    def __init__(self, status: int, operation_id: str, message: str = "", body: str = ""):
        self.status = status
        self.operation_id = operation_id
        self.message = message
        self.body = body
        super().__init__(
            "%s failed with HTTP %d%s" % (operation_id, status, ": " + message if message else "")
        )


class AuthenticationError(ApiError):
    """acquireToken was rejected, or a token was refused as invalid or released."""


class ReportGenerationFailed(VcfOperationsError):
    """Report generation reached the terminal failure status.

    Attributes:
        report_id: identifier of the report that failed.
        report: the last report object returned by getReport.
    """

    def __init__(self, report_id: str, report: dict):
        self.report_id = report_id
        self.report = report
        super().__init__(
            "report %s finished in terminal status %s"
            % (report_id, report.get("status", "<unknown>"))
        )


class ReportTimeout(VcfOperationsError):
    """Report generation did not reach a terminal status before the poll timeout.

    Attributes:
        report_id: identifier of the report still in flight.
        last_status: the most recent status observed.
        polls: how many getReport calls were made.
        elapsed: seconds spent polling.
    """

    def __init__(self, report_id: str, last_status: str, polls: int, elapsed: float):
        self.report_id = report_id
        self.last_status = last_status
        self.polls = polls
        self.elapsed = elapsed
        super().__init__(
            "report %s was still %s after %d polls over %.1fs"
            % (report_id, last_status, polls, elapsed)
        )

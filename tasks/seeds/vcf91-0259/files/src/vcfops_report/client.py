"""VCF Operations report client.

Every method below is a stub. Implement them against docs/contract.json using
only the Python standard library.
"""

from __future__ import annotations

from .errors import (  # noqa: F401  (imported for implementors to raise)
    ApiError,
    AuthenticationError,
    ReportGenerationFailed,
    ReportTimeout,
    VcfOperationsError,
)
from .models import ReportResult


class VcfOperationsClient:
    """Drives the createReport / getReport / downloadReport workflow.

    The client is responsible for the wire shape of every request it sends.
    docs/contract.json is the authority: it names the five operations that may
    be called, their methods and paths, the Authorization header format, the
    request-body properties, and the rule that an optional field the caller did
    not set is absent from the request rather than present and empty.
    """

    def __init__(
        self,
        base_url,
        username,
        password,
        auth_source=None,
        poll_interval=5.0,
        poll_timeout=600.0,
        request_timeout=30.0,
    ):
        """Configure a client.

        Args:
            base_url: root of the API including the contract's basePath, e.g.
                ``https://vcf-ops.example.com/suite-api``. A trailing slash is
                tolerated here and must not leak into request paths.
            username: user to authenticate as.
            password: that user's password.
            auth_source: optional authentication source name. When None the
                caller has not set it.
            poll_interval: seconds to wait between getReport calls.
            poll_timeout: seconds to keep polling before giving up on a report.
            request_timeout: per-request socket timeout in seconds.
        """
        raise NotImplementedError

    # -- session ----------------------------------------------------------

    @property
    def token(self):
        """The token currently held, or None if no token is held.

        Set by acquire_token; cleared by release_token.
        """
        raise NotImplementedError

    def acquire_token(self):
        """Invoke acquireToken and retain the token for later calls.

        Returns:
            The token string.

        Raises:
            AuthenticationError: the credentials were rejected (HTTP 401).
            ApiError: any other non-success status.
        """
        raise NotImplementedError

    def release_token(self):
        """Invoke releaseToken for the token currently held, then drop it.

        Does nothing when no token is held. After this returns, ``token`` is
        None.
        """
        raise NotImplementedError

    # -- report operations ------------------------------------------------

    def create_report(
        self,
        report_definition_id,
        resource_id,
        name=None,
        description=None,
        subject=None,
        publish=None,
    ):
        """Invoke createReport to start generating a report.

        The optional arguments correspond to the contract's
        callerSettableOptionalProperties. An argument left at None was not set
        by the caller. Note that ``publish=False`` *was* set by the caller.

        The returned report has been started, not finished; its status is not
        evidence that generation is complete.

        Returns:
            The report object from the response, as a dict.

        Raises:
            ApiError: on any non-success status.
        """
        raise NotImplementedError

    def get_report(self, report_id):
        """Invoke getReport once and return the report object as a dict.

        Raises:
            ApiError: on any non-success status.
        """
        raise NotImplementedError

    def wait_for_report(self, report_id):
        """Poll getReport until the report reaches a terminal status.

        Polling starts immediately -- the caller has only been told the report
        was accepted, so the current status must be observed rather than
        assumed. Between polls, wait ``poll_interval`` seconds. Give up once
        ``poll_timeout`` seconds have elapsed since the first poll.

        Returns:
            The report object whose status is the contract's successStatus.

        Raises:
            ReportGenerationFailed: a terminal failure status was reached.
            ReportTimeout: no terminal status within poll_timeout. The
                exception carries the last status seen and the poll count.
            ApiError: on any non-success HTTP status.
        """
        raise NotImplementedError

    def download_report(self, report_id, report_format=None):
        """Invoke downloadReport and return the report bytes.

        Args:
            report_id: the report to download.
            report_format: one of the contract's formatValues, or None when the
                caller has not chosen a format and the server default applies.

        Returns:
            The response body as bytes.

        Raises:
            ApiError: on any non-success status, including an attempt to
                download a report that is not yet complete.
        """
        raise NotImplementedError

    # -- end-to-end -------------------------------------------------------

    def generate_report(
        self,
        report_definition_id,
        resource_id,
        report_format=None,
        name=None,
        description=None,
        subject=None,
        publish=None,
    ):
        """Run the whole workflow: create, poll to a terminal status, download.

        Acquires a token first if none is held. Does not release the token --
        that is ``close``'s job.

        Returns:
            A ReportResult.

        Raises:
            ReportGenerationFailed: generation reached the failure status. No
                download is attempted in that case.
            ReportTimeout: generation never reached a terminal status.
            ApiError: on any non-success HTTP status.
        """
        raise NotImplementedError

    # -- lifecycle --------------------------------------------------------

    def close(self):
        """Release the token if one is held. Safe to call more than once."""
        raise NotImplementedError

    def __enter__(self):
        raise NotImplementedError

    def __exit__(self, exc_type, exc, tb):
        """Close the client, including when the body raised."""
        raise NotImplementedError

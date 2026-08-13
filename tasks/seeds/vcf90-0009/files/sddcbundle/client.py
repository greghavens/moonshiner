"""Client for the SDDC Manager bundle-download workflow.

The public surface below is what ``verify/verify_seed.py`` drives; keep the
names and signatures as they are and fill in the bodies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import (  # noqa: F401  (re-exported for implementers)
    ApiError,
    AuthenticationError,
    SddcManagerError,
    TaskFailedError,
    TaskTimeoutError,
)

#: Statuses that mean "still running" -- keep polling.
NON_TERMINAL_STATUSES = frozenset({"PENDING", "IN_PROGRESS"})
#: Terminal statuses that mean the operation finished acceptably.
TERMINAL_SUCCESS_STATUSES = frozenset({"SUCCESSFUL", "COMPLETED_WITH_WARNING"})
#: Terminal statuses that mean the operation did not succeed.
TERMINAL_FAILURE_STATUSES = frozenset({"FAILED", "CANCELLED", "SKIPPED"})


def normalize_status(status):
    """Normalize an SDDC Manager task status to upper snake case.

    The 9.0.0.0 specification documents the same status in several spellings
    (for example ``In Progress`` and ``IN_PROGRESS``), so comparisons must not
    be done against a single literal form.
    """
    raise NotImplementedError


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a bundle download that was polled to a terminal state."""

    bundle_id: str
    task_id: str
    status: str  # normalized, e.g. "SUCCESSFUL"
    raw_status: str  # exactly as returned by the API
    polls: int
    task: dict = field(default_factory=dict)


class BundleDownloadClient:
    """Talks to one SDDC Manager instance over the pinned contract."""

    def __init__(
        self,
        base_url,
        username,
        password,
        *,
        timeout=30.0,
        poll_interval=2.0,
        poll_timeout=3600.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._access_token = None

    # -- operations --------------------------------------------------------
    def authenticate(self):
        """Return an access token, creating one on first use and caching it."""
        raise NotImplementedError

    def start_bundle_download(
        self,
        bundle_id,
        *,
        download_now=None,
        scheduled_timestamp=None,
        cancel_now=None,
    ):
        """Kick off the asynchronous bundle download and return the Task body."""
        raise NotImplementedError

    def get_task(self, task_id):
        """Fetch a single Task by id."""
        raise NotImplementedError

    def wait_for_task(self, task_id, *, poll_interval=None, poll_timeout=None):
        """Poll a task until it reaches a terminal state.

        Returns ``(task, polls)``.
        """
        raise NotImplementedError

    def download_bundle(
        self,
        bundle_id,
        *,
        download_now=None,
        scheduled_timestamp=None,
        cancel_now=None,
        poll_interval=None,
        poll_timeout=None,
    ):
        """Start the download and poll it to a terminal state."""
        raise NotImplementedError

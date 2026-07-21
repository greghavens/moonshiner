"""Scheduled leader work protected by coordinator fencing tokens."""

from __future__ import annotations

from dataclasses import dataclass

from .coordinator import FakeCoordinator, Lease
from .scheduler import ManualScheduler, ScheduledCall


class CancellationToken:
    """Cancellation state shared with work performed during one tenure."""

    def __init__(self) -> None:
        self.cancelled = False
        self.cancel_count = 0

    def cancel(self) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        self.cancel_count += 1


@dataclass(frozen=True)
class WriteRecord:
    at: float
    owner: str
    fence: int


@dataclass(frozen=True)
class RejectedWrite:
    at: float
    owner: str
    fence: int
    current_fence: int


class WriteSession:
    """A tenure-scoped resource used for writes and closed during cleanup."""

    def __init__(
        self,
        journal: "FencedJournal",
        owner: str,
        fence: int,
        cancellation: CancellationToken,
    ) -> None:
        self._journal = journal
        self.owner = owner
        self.fence = fence
        self.cancellation = cancellation
        self.closed = False
        self.close_count = 0

    def append(self, at: float) -> bool:
        if self.closed:
            raise RuntimeError("write session is closed")
        if self.cancellation.cancelled:
            raise RuntimeError("leadership work was cancelled")
        return self._journal._append(at, self.owner, self.fence)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_count += 1


class FencedJournal:
    """Accept writes at the newest observed fence and reject older ones."""

    def __init__(self) -> None:
        self.records: list[WriteRecord] = []
        self.rejected: list[RejectedWrite] = []
        self.sessions: list[WriteSession] = []
        self._current_fence = 0

    def open_session(
        self, owner: str, fence: int, cancellation: CancellationToken
    ) -> WriteSession:
        session = WriteSession(self, owner, fence, cancellation)
        self.sessions.append(session)
        return session

    def _append(self, at: float, owner: str, fence: int) -> bool:
        if fence < self._current_fence:
            self.rejected.append(
                RejectedWrite(at, owner, fence, self._current_fence)
            )
            return False
        self._current_fence = fence
        self.records.append(WriteRecord(at, owner, fence))
        return True


@dataclass
class _Tenure:
    lease: Lease
    cancellation: CancellationToken
    session: WriteSession
    pending: ScheduledCall | None = None


class LeaderLoop:
    """Campaign once, then write periodically for as long as the lease lives."""

    def __init__(
        self,
        owner: str,
        coordinator: FakeCoordinator,
        scheduler: ManualScheduler,
        journal: FencedJournal,
        interval: float = 5,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.owner = owner
        self._coordinator = coordinator
        self._scheduler = scheduler
        self._journal = journal
        self._interval = interval
        self._running = False
        self._tenure: _Tenure | None = None

    @property
    def is_leader(self) -> bool:
        return self._tenure is not None

    @property
    def current_fence(self) -> int | None:
        return None if self._tenure is None else self._tenure.lease.fence

    @property
    def current_session(self) -> WriteSession | None:
        return None if self._tenure is None else self._tenure.session

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._coordinator.request(
            self.owner, self._lease_granted, self._lease_lost
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._coordinator.cancel_request(self.owner)
        if self._tenure is not None:
            self._retire(self._tenure)

    def _lease_granted(self, lease: Lease) -> None:
        if not self._running:
            self._coordinator.release(lease)
            return
        cancellation = CancellationToken()
        session = self._journal.open_session(
            self.owner, lease.fence, cancellation
        )
        tenure = _Tenure(lease, cancellation, session)
        self._tenure = tenure
        self._schedule(tenure, 0)

    def _lease_lost(self, lease: Lease) -> None:
        tenure = self._tenure
        if tenure is None or tenure.lease is not lease:
            return

        # Lease expiry is currently treated as an informational renewal event.
        # The periodic callback is left running until somebody stops the loop.
        return

    def _schedule(self, tenure: _Tenure, delay: float) -> None:
        tenure.pending = self._scheduler.call_later(
            delay, lambda: self._write_once(tenure)
        )

    def _write_once(self, tenure: _Tenure) -> None:
        if self._tenure is not tenure:
            return
        tenure.session.append(self._scheduler.now)
        self._schedule(tenure, self._interval)

    def _retire(self, tenure: _Tenure) -> None:
        if self._tenure is not tenure:
            return
        tenure.cancellation.cancel()
        if tenure.pending is not None:
            tenure.pending.cancel()
        tenure.session.close()
        self._tenure = None
        self._coordinator.release(tenure.lease)

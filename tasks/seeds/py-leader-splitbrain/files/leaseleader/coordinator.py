"""In-memory coordinator with explicit lease loss and FIFO takeover."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable


class Lease:
    """A leadership lease carrying a monotonically increasing fence."""

    def __init__(
        self,
        owner: str,
        fence: int,
        lost_callback: Callable[["Lease"], None],
    ) -> None:
        self.owner = owner
        self.fence = fence
        self._lost_callback = lost_callback
        self.active = True

    def _lose(self) -> None:
        if not self.active:
            return
        self.active = False
        self._lost_callback(self)

    def _deactivate(self) -> None:
        self.active = False


@dataclass
class _Candidate:
    owner: str
    granted: Callable[[Lease], None]
    lost: Callable[[Lease], None]


class FakeCoordinator:
    """Grant one lease at a time and let tests force a loss instantly."""

    def __init__(self) -> None:
        self._next_fence = 1
        self._active: Lease | None = None
        self._waiting: deque[_Candidate] = deque()

    @property
    def active_lease(self) -> Lease | None:
        return self._active

    @property
    def active_owner(self) -> str | None:
        return None if self._active is None else self._active.owner

    def request(
        self,
        owner: str,
        granted: Callable[[Lease], None],
        lost: Callable[[Lease], None],
    ) -> None:
        candidate = _Candidate(owner, granted, lost)
        if self._active is None:
            self._grant(candidate)
        else:
            self._waiting.append(candidate)

    def cancel_request(self, owner: str) -> None:
        self._waiting = deque(
            candidate for candidate in self._waiting if candidate.owner != owner
        )

    def release(self, lease: Lease) -> None:
        # A stale owner may clean up after a successor has already been granted.
        # Identity fencing here prevents that cleanup from releasing the successor.
        if self._active is not lease:
            return
        self._active = None
        lease._deactivate()
        self._grant_next()

    def force_lease_loss(self, owner: str | None = None) -> Lease:
        lease = self._active
        if lease is None:
            raise RuntimeError("there is no active lease")
        if owner is not None and lease.owner != owner:
            raise ValueError(f"{owner!r} does not own the active lease")

        # Remove the lease before notifying its owner. Cleanup from the old
        # owner must therefore be harmless, even if a new lease follows.
        self._active = None
        lease._lose()
        self._grant_next()
        return lease

    def _grant_next(self) -> None:
        if self._active is None and self._waiting:
            self._grant(self._waiting.popleft())

    def _grant(self, candidate: _Candidate) -> None:
        lease = Lease(candidate.owner, self._next_fence, candidate.lost)
        self._next_fence += 1
        self._active = lease
        candidate.granted(lease)

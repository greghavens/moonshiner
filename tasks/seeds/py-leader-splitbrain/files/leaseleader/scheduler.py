"""A tiny scheduler whose clock only moves when a test advances it."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
from typing import Callable


class ScheduledCall:
    """A cancellable callback owned by :class:`ManualScheduler`."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if not self._cancelled:
            self._callback()


@dataclass(order=True)
class _QueuedCall:
    deadline: float
    sequence: int
    call: ScheduledCall = field(compare=False)


class ManualScheduler:
    """Run callbacks deterministically without consulting a wall clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self._next_sequence = 0
        self._queue: list[_QueuedCall] = []

    def call_later(
        self, delay: float, callback: Callable[[], None]
    ) -> ScheduledCall:
        if delay < 0:
            raise ValueError("delay must be non-negative")
        call = ScheduledCall(callback)
        queued = _QueuedCall(self.now + delay, self._next_sequence, call)
        self._next_sequence += 1
        heapq.heappush(self._queue, queued)
        return call

    def run_ready(self) -> None:
        self.advance(0)

    def advance(self, amount: float) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        target = self.now + amount
        while self._queue and self._queue[0].deadline <= target:
            queued = heapq.heappop(self._queue)
            self.now = queued.deadline
            queued.call.run()
        self.now = target

    @property
    def pending_count(self) -> int:
        return sum(not queued.call.cancelled for queued in self._queue)

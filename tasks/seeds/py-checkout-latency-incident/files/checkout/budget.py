"""Logical request budgets. No code in this package reads the wall clock."""


class RequestBudget:
    def __init__(self, clock, timeout_ms):
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        self._clock = clock
        self.started_at_ms = clock.now_ms()
        self.deadline_ms = self.started_at_ms + timeout_ms

    def remaining_ms(self):
        return max(0, self.deadline_ms - self._clock.now_ms())


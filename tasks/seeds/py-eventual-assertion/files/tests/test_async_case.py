import threading
import unittest
from unittest.mock import patch

from eventual_assertion import testing


class DeferredThread:
    """A thread whose target runs only when the test scheduler releases it."""

    instances = []

    def __init__(self, *, target, name=None):
        self._target = target
        self._name = name
        self._real_thread = None
        self.__class__.instances.append(self)

    def start(self):
        # Deliberately defer execution to model a busy scheduler.
        return None

    def release(self):
        if self._real_thread is None:
            self._real_thread = threading.Thread(
                target=self._target,
                name=self._name,
            )
            self._real_thread.start()

    def join(self, timeout=None):
        self.release()
        self._real_thread.join(timeout)

    def is_alive(self):
        return self._real_thread is not None and self._real_thread.is_alive()


class SchedulerEvent:
    """Release deferred workers when the main thread waits for their signal."""

    def __init__(self):
        self._event = threading.Event()

    def set(self):
        self._event.set()

    def wait(self, timeout=None):
        for worker in list(DeferredThread.instances):
            worker.release()
        return self._event.wait(timeout)


class AsyncRetryCaseTests(unittest.TestCase):
    def setUp(self):
        DeferredThread.instances = []

    def test_first_failed_attempt_is_synchronized_not_slept(self):
        # Fixed sleeps do not make progress under this controlled scheduler.
        # An Event handshake does: waiting releases the worker, whose first
        # failing assertion signals after observing the not-ready state.
        eventual_calls = []
        real_assert_eventually = testing.assert_eventually

        def recording_assert_eventually(assertion, *, timeout, interval):
            eventual_calls.append((timeout, interval))
            return real_assert_eventually(
                assertion,
                timeout=timeout,
                interval=interval,
            )

        with (
            patch.object(testing, "Thread", DeferredThread),
            patch.object(testing, "Event", SchedulerEvent, create=True),
            patch.object(testing, "sleep", lambda _seconds: None, create=True),
            patch.object(
                testing,
                "assert_eventually",
                recording_assert_eventually,
            ),
        ):
            attempts = testing.run_async_retry_case(timeout=0.2, interval=0.001)

        self.assertGreaterEqual(attempts, 2)
        self.assertEqual(eventual_calls, [(0.2, 0.001)])


if __name__ == "__main__":
    unittest.main()

"""Reusable scenarios for testing :func:`assert_eventually`."""

from threading import Thread
from time import sleep

from .core import assert_eventually


def run_async_retry_case(*, timeout=0.25, interval=0.001):
    """Exercise a failed assertion followed by asynchronous success.

    Return the number of assertion attempts.  A valid run must make at least two
    attempts, proving that the eventual assertion actually retried.
    """

    state = {"ready": False, "attempts": 0}
    failures = []

    def assert_ready():
        state["attempts"] += 1
        if not state["ready"]:
            raise AssertionError("service is not ready")

    def run_assertion():
        try:
            assert_eventually(assert_ready, timeout=timeout, interval=interval)
        except BaseException as error:
            failures.append(error)

    worker = Thread(target=run_assertion, name="eventual-assertion")
    worker.start()

    # Give the worker time to make its first, failing assertion attempt.
    sleep(0.01)
    state["ready"] = True

    worker.join(timeout=1.0)
    if worker.is_alive():
        raise RuntimeError("eventual assertion worker did not finish")
    if failures:
        raise failures[0]
    if state["attempts"] < 2:
        raise AssertionError("eventual assertion did not perform a retry")
    return state["attempts"]

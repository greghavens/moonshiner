"""Production eventual-assertion behavior."""

from time import monotonic, sleep


def assert_eventually(assertion, *, timeout=0.25, interval=0.001):
    """Retry ``assertion`` until it passes or its timeout expires.

    The last ``AssertionError`` is intentionally re-raised so callers retain the
    assertion's original type, message, and traceback rather than receiving a
    wrapper timeout exception.
    """

    if timeout < 0:
        raise ValueError("timeout must not be negative")
    if interval <= 0:
        raise ValueError("interval must be positive")

    deadline = monotonic() + timeout
    while True:
        try:
            assertion()
        except AssertionError:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise
            sleep(min(interval, remaining))
        else:
            return

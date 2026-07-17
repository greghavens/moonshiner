"""Acceptance tests for the retry decorator. Run: python3 test_retrying.py"""


class Flaky:
    """Callable that fails `failures` times, then returns `result`."""

    def __init__(self, failures, result="ok", exc=IOError):
        self.failures = failures
        self.result = result
        self.exc = exc
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc(f"boom #{self.calls}")
        return self.result


def main():
    from retrying import retry, fixed, exponential

    # -- immediate success: one call, zero sleeps --
    slept = []
    f = Flaky(failures=0)
    wrapped = retry(attempts=3, backoff=fixed(0.5), sleep=slept.append)(f)
    assert wrapped() == "ok"
    assert f.calls == 1
    assert slept == []

    # -- two failures then success, fixed backoff --
    slept = []
    f = Flaky(failures=2)
    wrapped = retry(attempts=3, backoff=fixed(0.5), sleep=slept.append)(f)
    assert wrapped() == "ok"
    assert f.calls == 3
    assert slept == [0.5, 0.5]

    # -- exponential backoff with a cap --
    slept = []
    f = Flaky(failures=5)
    wrapped = retry(attempts=6, backoff=exponential(1.0, multiplier=2.0, max_delay=5.0),
                    sleep=slept.append)(f)
    assert wrapped() == "ok"
    assert slept == [1.0, 2.0, 4.0, 5.0, 5.0], slept

    # -- exhaustion re-raises the LAST exception instance, unwrapped --
    slept = []
    f = Flaky(failures=99)
    wrapped = retry(attempts=3, backoff=fixed(0.1), sleep=slept.append)(f)
    try:
        wrapped()
        assert False, "should have raised"
    except IOError as e:
        assert str(e) == "boom #3", e
    assert f.calls == 3
    assert slept == [0.1, 0.1], "no sleep after the final attempt"

    # -- retry_on filters which exceptions are retryable --
    slept = []
    f = Flaky(failures=99, exc=KeyError)
    wrapped = retry(attempts=3, backoff=fixed(0.1), retry_on=(IOError,),
                    sleep=slept.append)(f)
    try:
        wrapped()
        assert False
    except KeyError:
        pass
    assert f.calls == 1, "non-retryable exception must not be retried"
    assert slept == []

    # -- subclasses of a retry_on entry are retryable --
    slept = []
    f = Flaky(failures=1, exc=ConnectionResetError)  # subclass of OSError
    wrapped = retry(attempts=2, backoff=fixed(0.1), retry_on=(OSError,),
                    sleep=slept.append)(f)
    assert wrapped() == "ok"
    assert f.calls == 2

    # -- give_up_on wins even when retry_on matches --
    slept = []
    f = Flaky(failures=99, exc=ValueError)
    wrapped = retry(attempts=5, backoff=fixed(0.1), retry_on=(Exception,),
                    give_up_on=(ValueError,), sleep=slept.append)(f)
    try:
        wrapped()
        assert False
    except ValueError:
        pass
    assert f.calls == 1
    assert slept == []

    # -- on_retry hook fires before each sleep with (exc, attempt, delay) --
    events = []
    slept = []

    def hook(exc, attempt, delay):
        events.append((type(exc).__name__, str(exc), attempt, delay))
        assert len(slept) == attempt - 1, "hook must fire before its sleep"

    f = Flaky(failures=2)
    wrapped = retry(attempts=3, backoff=fixed(0.25), sleep=slept.append,
                    on_retry=hook)(f)
    assert wrapped() == "ok"
    assert events == [
        ("OSError", "boom #1", 1, 0.25),
        ("OSError", "boom #2", 2, 0.25),
    ], events

    # -- arguments and return values pass through untouched --
    @retry(attempts=2, backoff=fixed(0.0), sleep=lambda d: None)
    def add(a, b, *, scale=1):
        return (a + b) * scale

    assert add(2, 3, scale=10) == 50
    assert add.__name__ == "add", "wrapper must preserve the function identity"

    # -- attempts=1 means exactly one call and no sleeping --
    slept = []
    f = Flaky(failures=1)
    wrapped = retry(attempts=1, backoff=fixed(9.9), sleep=slept.append)(f)
    try:
        wrapped()
        assert False
    except IOError:
        pass
    assert f.calls == 1 and slept == []

    # -- bad configuration fails fast at decoration time --
    for bad_attempts in (0, -2):
        try:
            retry(attempts=bad_attempts, backoff=fixed(1.0), sleep=lambda d: None)
            assert False, "attempts < 1 should raise ValueError"
        except ValueError:
            pass
    try:
        fixed(-1.0)
        assert False, "negative fixed delay should raise ValueError"
    except ValueError:
        pass
    for bad in ((0.0,), (-1.0,), (1.0, 0.5)):
        try:
            exponential(*bad)
            assert False, f"exponential{bad} should raise ValueError"
        except ValueError:
            pass

    # -- policies are plain callables: retry index (1-based) -> delay --
    pol = exponential(0.5, multiplier=3.0)
    assert pol(1) == 0.5 and pol(2) == 1.5 and pol(3) == 4.5
    assert fixed(2.0)(7) == 2.0

    print("all retrying checks passed")


if __name__ == "__main__":
    main()

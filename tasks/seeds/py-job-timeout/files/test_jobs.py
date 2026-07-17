"""Acceptance checks for jobs.py. Run: python3 test_jobs.py"""
import time

from jobs import JobFailed, JobRunner


# ---------------------------------------------------------------- existing

def test_add_validation_and_names():
    r = JobRunner()
    r.add("compact", lambda: 1).add("prune", lambda: 2)
    assert r.names() == ["compact", "prune"]
    assert len(r) == 2
    for name, fn in [("", lambda: 1), ("  ", lambda: 1), ("compact", lambda: 1)]:
        try:
            r.add(name, fn)
            assert False, "accepted bad job name %r" % (name,)
        except ValueError:
            pass
    try:
        r.add("static", 42)
        assert False, "non-callable job accepted"
    except TypeError:
        pass


def test_run_in_order_with_results():
    order = []
    r = (JobRunner()
         .add("a", lambda: order.append("a") or 10)
         .add("b", lambda: order.append("b") or 20))
    assert r.run() == [("a", 10), ("b", 20)]
    assert order == ["a", "b"]
    assert JobRunner().run() == []


def test_run_aborts_on_first_failure():
    order = []
    cause = RuntimeError("disk full")

    def explode():
        order.append("explode")
        raise cause

    r = (JobRunner()
         .add("ok1", lambda: order.append("ok1"))
         .add("explode", explode)
         .add("never", lambda: order.append("never")))
    try:
        r.run()
        assert False, "run() swallowed the failure"
    except JobFailed as e:
        assert e.job == "explode"
        assert e.cause is cause
    assert order == ["ok1", "explode"]  # 'never' must not have started


# ------------- feature: execute() with per-job timeout + error summary

def test_execute_all_ok():
    order = []
    r = (JobRunner()
         .add("a", lambda: order.append("a") or 1)
         .add("b", lambda: order.append("b") or 2))
    s = r.execute()
    got = [(rec.name, rec.status, rec.value, rec.error) for rec in s.records]
    assert got == [("a", "ok", 1, None), ("b", "ok", 2, None)]
    assert order == ["a", "b"]
    assert s.ok == ["a", "b"]
    assert s.errors == [] and s.timeouts == []
    assert s.all_ok


def test_execute_collects_errors_and_continues():
    order = []
    boom = ValueError("boom")

    def bad():
        order.append("bad")
        raise boom

    r = (JobRunner()
         .add("a", lambda: order.append("a") or "A")
         .add("bad", bad)
         .add("c", lambda: order.append("c") or "C"))
    s = r.execute(continue_on_error=True)
    assert [rec.status for rec in s.records] == ["ok", "error", "ok"]
    assert s.records[1].error is boom
    assert s.records[1].value is None
    assert order == ["a", "bad", "c"]
    assert s.errors == ["bad"] and s.ok == ["a", "c"]
    assert not s.all_ok


def test_execute_stops_at_first_failure_by_default():
    order = []

    def bad():
        order.append("bad")
        raise RuntimeError("nope")

    r = (JobRunner()
         .add("a", lambda: order.append("a"))
         .add("bad", bad)
         .add("c", lambda: order.append("c")))
    s = r.execute()
    assert [rec.name for rec in s.records] == ["a", "bad"]
    assert order == ["a", "bad"]  # c never started
    assert not s.all_ok


def test_execute_times_out_stuck_job_and_moves_on():
    order = []
    r = (JobRunner()
         .add("stuck", lambda: time.sleep(2.0))
         .add("quick", lambda: order.append("quick") or "q"))
    t0 = time.monotonic()
    s = r.execute(timeout=0.2, continue_on_error=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, "execute blocked on the stuck job (%.2fs)" % elapsed
    assert [(rec.name, rec.status) for rec in s.records] == [
        ("stuck", "timeout"), ("quick", "ok")]
    assert s.records[0].value is None and s.records[0].error is None
    assert s.timeouts == ["stuck"]
    assert order == ["quick"]
    assert not s.all_ok


def test_execute_timeout_respects_stop_policy():
    started = []
    r = (JobRunner()
         .add("stuck", lambda: time.sleep(2.0))
         .add("later", lambda: started.append("later")))
    t0 = time.monotonic()
    s = r.execute(timeout=0.2)  # continue_on_error defaults to False
    assert time.monotonic() - t0 < 1.5
    assert [rec.status for rec in s.records] == ["timeout"]
    assert started == []


def test_generous_timeout_is_not_a_timeout():
    r = JobRunner().add("nap", lambda: time.sleep(0.01) or "done")
    s = r.execute(timeout=5)
    assert s.records[0].status == "ok"
    assert s.records[0].value == "done"
    assert s.all_ok


def test_execute_validation_and_empty_runner():
    r = JobRunner().add("a", lambda: 1)
    for bad in (0, -1, -0.5):
        try:
            r.execute(timeout=bad)
            assert False, "accepted timeout %r" % (bad,)
        except ValueError:
            pass
    s = JobRunner().execute()
    assert s.records == [] and s.all_ok


EXISTING = [
    test_add_validation_and_names,
    test_run_in_order_with_results,
    test_run_aborts_on_first_failure,
]

FEATURE = [
    test_execute_all_ok,
    test_execute_collects_errors_and_continues,
    test_execute_stops_at_first_failure_by_default,
    test_execute_times_out_stuck_job_and_moves_on,
    test_execute_timeout_respects_stop_policy,
    test_generous_timeout_is_not_a_timeout,
    test_execute_validation_and_empty_runner,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()

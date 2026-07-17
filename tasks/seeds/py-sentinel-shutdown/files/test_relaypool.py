"""Behavior contract for the RelayPool coordinator.

Every scenario runs in its own process group with a 5-second hard timeout;
a coordinator that never returns is killed and reported as a failure.
"""

import json
import os
import signal
import subprocess
import sys


def run_scenario(name):
    proc = subprocess.Popen(
        [sys.executable, "scenarios.py", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
        raise AssertionError(f"scenario {name!r}: coordinator did not finish within 5s")
    assert proc.returncode == 0, f"scenario {name!r} exited {proc.returncode}:\n{err}"
    return json.loads(out)


def check_common(name, snap):
    assert snap["done_signals"] == snap["workers"], (
        f"{name}: expected exactly one terminal signal per worker, "
        f"got {snap['done_signals']} for {snap['workers']} workers"
    )
    assert snap["alive"] == [], f"{name}: worker threads left running: {snap['alive']}"
    assert snap["leftover"] == 0, f"{name}: undrained messages: {snap['leftover']}"


def test_clean_run():
    snap = run_scenario("clean")
    assert snap["outcomes"] == [
        ["ok", "j1", "A"],
        ["ok", "j2", "B"],
        ["ok", "j3", "C"],
        ["ok", "j4", "D"],
        ["ok", "j5", "E"],
    ], snap["outcomes"]
    check_common("clean", snap)


def test_no_jobs():
    snap = run_scenario("no-jobs")
    assert snap["outcomes"] == []
    check_common("no-jobs", snap)


def test_one_failing_job_still_completes():
    snap = run_scenario("one-failure")
    assert snap["outcomes"] == [
        ["error", "j2", "RuntimeError: jam: boom-2"],
        ["ok", "j1", "A"],
        ["ok", "j3", "C"],
        ["ok", "j4", "D"],
    ], snap["outcomes"]
    check_common("one-failure", snap)


def test_failure_on_first_job():
    snap = run_scenario("failure-first")
    assert snap["outcomes"] == [
        ["error", "j1", "RuntimeError: jam: boom-1"],
        ["ok", "j2", "B"],
        ["ok", "j3", "C"],
    ], snap["outcomes"]
    check_common("failure-first", snap)


def test_single_worker_survives_failure_and_drains():
    snap = run_scenario("single-worker")
    assert snap["outcomes"] == [
        ["error", "j1", "RuntimeError: jam: boom-1"],
        ["ok", "j2", "AFTER"],
    ], "the job queued behind the failing one must still be processed"
    check_common("single-worker", snap)


def test_every_job_failing_still_terminates():
    snap = run_scenario("all-failures")
    assert snap["outcomes"] == [
        ["error", "j1", "RuntimeError: jam: boom-1"],
        ["error", "j2", "RuntimeError: jam: boom-2"],
        ["error", "j3", "RuntimeError: jam: boom-3"],
    ], snap["outcomes"]
    check_common("all-failures", snap)


def main():
    tests = [fn for name, fn in sorted(list(globals().items())) if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} checks passed")


if __name__ == "__main__":
    main()

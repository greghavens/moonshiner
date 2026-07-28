"""Updating must not cost work in flight.

A claimed job holds a lease and represents metered teacher and judge spend plus
one of a limited attempt allowance. Replacing the release under a running
coordinator forfeits that, so the updater drains first and refuses to proceed
if anything is still working.

Draining is a property of the *queue*: the moment nothing is executing in it,
it is drained. A job process spans several attempts and can run for hours, so
waiting for the process to exit is not draining — the pause between two
attempts is, and stopping there costs nothing.
"""
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _updater():
    """Load the update helpers without importing the whole CLI module."""
    source = (ROOT / "moonshiner.py").read_text()
    start = source.index("MOONSHINER_UNIT = re.compile")
    end = source.index("def _update(argv")
    import os, re, signal, subprocess, time
    namespace = {"re": re, "os": os, "signal": signal, "subprocess": subprocess,
                 "time": time, "sys": sys, "Path": pathlib.Path}
    exec(compile(source[start:end], "moonshiner-update", "exec"), namespace)
    return namespace


class UnitDiscovery(unittest.TestCase):
    def setUp(self):
        self.ns = _updater()

    def test_matches_only_project_scoped_queue_units(self):
        match = self.ns["MOONSHINER_UNIT"].match
        self.assertTrue(match("moonshiner-trace-continuous-39fd2040b76f.service"))
        self.assertTrue(match("moonshiner-publish-b5ae23d72506.service"))
        self.assertTrue(match("moonshiner-synthetic-corrections-39fd2040b76f.service"))
        # Not ours, and must never be stopped by an update.
        self.assertFalse(match("seed-sync.service"))
        self.assertFalse(match("triggerfish.service"))
        self.assertFalse(match("moonshiner-something.service"))

    def test_activating_units_are_included(self):
        """A queue restarting between attempts is still a queue."""
        source = (ROOT / "moonshiner.py").read_text()
        block = source[source.index("def _running_units"):source.index("def _unit_project")]
        self.assertIn("activating", block)


class JobState(unittest.TestCase):
    def setUp(self):
        self.ns = _updater()

    def test_a_job_is_working_only_while_something_runs_beneath_it(self):
        listing = "\n".join([
            "  PID  PPID CMD",
            "  100     1 /usr/bin/python moonshiner run --all --yes",
            "  200   100 /usr/bin/python moonshiner run --only go-csvlimits",
            "  300   200 bwrap --die-with-parent -- pi --print --mode json",
            "  400   100 /usr/bin/python moonshiner run --only go-hmac-compare",
        ])
        with mock.patch.object(self.ns["subprocess"], "run",
                               return_value=mock.Mock(stdout=listing)):
            jobs = dict(self.ns["_jobs"]())
        self.assertTrue(jobs[200], "a job with a harness under it is working")
        self.assertFalse(jobs[400], "a job between attempts is not working")


class Draining(unittest.TestCase):
    def setUp(self):
        self.ns = _updater()

    def test_refuses_to_stop_while_work_is_executing(self):
        calls = []
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_jobs": lambda *_: [(11, True), (12, True)]}), \
             mock.patch.object(self.ns["time"], "monotonic", side_effect=[0, 10_000]), \
             mock.patch.object(self.ns["time"], "sleep"):
            drained = self.ns["_drain_and_stop"](["moonshiner-trace-continuous-aaaaaaaaaaaa"], 1)
        self.assertFalse(drained, "must not report success while jobs run")
        flat = [" ".join(c) for c in calls]
        self.assertFalse(any("stop" in c for c in flat),
                         "no queue may be stopped while a job is running")
        self.assertTrue(any("SIGCONT" in c for c in flat),
                        "a refused drain must resume the coordinator")

    def test_a_job_paused_between_attempts_counts_as_drained(self):
        """The requirement: once nothing is running, the queue is drained.

        The job process is still alive and holds a lease with attempts left —
        it just isn't executing anything this instant. That is the gap, and
        the updater must take it instead of waiting out the whole job.
        """
        calls, signalled = [], []
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_jobs": lambda *_: [(4242, False)]}), \
             mock.patch.object(self.ns["os"], "kill",
                               side_effect=lambda pid, sig: signalled.append((pid, sig))), \
             mock.patch.object(self.ns["time"], "sleep"):
            drained = self.ns["_drain_and_stop"](["moonshiner-trace-continuous-aaaaaaaaaaaa"], 60)
        self.assertTrue(drained, "an idle job is a drained queue")
        self.assertIn((4242, self.ns["signal"].SIGSTOP), signalled,
                      "the idle job must be frozen so it cannot start another attempt")
        self.assertTrue(any("stop" in " ".join(c) for c in calls),
                        "must stop the queue once it stands still")

    def test_a_job_that_starts_an_attempt_while_being_frozen_is_resumed(self):
        """Losing the race must cost nothing: resume it and keep waiting."""
        states = [[(4242, False)], [(4242, True)]]
        signalled = []
        with mock.patch.dict(self.ns, {"_jobs": lambda *_: states[-1]}), \
             mock.patch.object(self.ns["os"], "kill",
                               side_effect=lambda pid, sig: signalled.append((pid, sig))):
            held = self.ns["_hold_between_attempts"](4242)
        self.assertFalse(held, "a job that started an attempt is not held")
        self.assertEqual([(4242, self.ns["signal"].SIGSTOP),
                          (4242, self.ns["signal"].SIGCONT)], signalled)

    def test_stops_when_nothing_is_claimed_at_all(self):
        calls = []
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_jobs": lambda *_: []}), \
             mock.patch.object(self.ns["time"], "sleep"):
            drained = self.ns["_drain_and_stop"](["moonshiner-trace-continuous-aaaaaaaaaaaa"], 60)
        self.assertTrue(drained)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("SIGSTOP" in c for c in flat), "must pause claiming first")
        self.assertTrue(any("stop" in c for c in flat), "must stop once drained")
        self.assertLess(next(i for i, c in enumerate(flat) if "SIGSTOP" in c),
                        next(i for i, c in enumerate(flat) if "stop" in c),
                        "pause must precede stop")




class SeedAuthoringIsIndivisible(unittest.TestCase):
    """An authoring job has no checkpoint, so it is never stopped part way.

    A trace attempt is recorded in the ledger, so pausing between two attempts
    costs nothing. Authoring one seed is a single piece of paid work: stopping
    it discards all of it and pays again from the start.
    """

    def setUp(self):
        self.ns = _updater()

    def test_an_idle_authoring_job_still_counts_as_working(self):
        listing = "\n".join([
            "  PID  PPID CMD",
            "  100     1 /usr/bin/python moonshiner seed queue --yes",
            "  200   100 /usr/bin/python moonshiner seed run --id vcf91-0001 --brief x",
            "  300     1 /usr/bin/python moonshiner run --only go-csvlimits",
        ])
        with mock.patch.object(self.ns["subprocess"], "run",
                               return_value=mock.Mock(stdout=listing)):
            jobs = dict(self.ns["_jobs"]())
        self.assertTrue(jobs[200], "authoring with no child is still working")
        self.assertFalse(jobs[300], "a trace between attempts is not working")

    def test_a_drain_waits_for_authoring_rather_than_freezing_it(self):
        calls, signalled = [], []
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_jobs": lambda *_: [(200, True)]}), \
             mock.patch.object(self.ns["os"], "kill",
                               side_effect=lambda pid, sig: signalled.append((pid, sig))), \
             mock.patch.object(self.ns["time"], "monotonic", side_effect=[0, 10_000]), \
             mock.patch.object(self.ns["time"], "sleep"):
            drained = self.ns["_drain_and_stop"](["moonshiner-seed-queue-aaaaaaaaaaaa"], 1)
        self.assertFalse(drained)
        self.assertEqual([], signalled, "an authoring job must never be frozen")
        self.assertFalse(any("stop" in " ".join(c) for c in calls),
                         "the queue must not be stopped under a running author")

class InlineWorkersAreDrainedToo(unittest.TestCase):
    """The correction queue does its metered work in its own process.

    Nothing in the process table names it as a job, so the drain did not see
    it and the update stopped its unit mid-correction — a metered review
    thrown away for exactly the reason this feature exists to prevent.
    """

    def setUp(self):
        self.ns = _updater()

    def test_the_correction_queue_main_process_is_watched(self):
        def dispatch(args, **_kwargs):
            if args[:2] == ["systemctl", "--user"] and "show" in args:
                return mock.Mock(stdout="777\n")
            return mock.Mock(stdout="")
        with mock.patch.object(self.ns["subprocess"], "run", side_effect=dispatch):
            pids = self.ns["_inline_workers"](
                ["moonshiner-synthetic-corrections-aaaaaaaaaaaa",
                 "moonshiner-trace-continuous-aaaaaaaaaaaa"])
        self.assertEqual([777], pids, "only in-process queues are watched this way")

    def test_it_is_working_while_a_runtime_runs_beneath_it(self):
        listing = "\n".join([
            "  PID  PPID CMD",
            "  777     1 /usr/bin/python moonshiner synthetic-corrections run --watch --yes",
            "  888   777 node /usr/bin/codex exec --json --model gpt-5.6-sol",
            "  999     1 /usr/bin/python moonshiner synthetic-corrections run --watch --yes",
        ])
        with mock.patch.object(self.ns["subprocess"], "run",
                               return_value=mock.Mock(stdout=listing)):
            jobs = dict(self.ns["_jobs"]([777, 999]))
        self.assertTrue(jobs[777], "a correction in progress is working")
        self.assertFalse(jobs[999], "an idle watcher is not working")


class APauseIsAlwaysUndone(unittest.TestCase):
    """An interrupted update must not leave the queues frozen.

    The drain pauses every coordinator before it can stop them. An updater
    killed in that window left seven coordinators in T state with nothing
    running and nothing to notice, until they were resumed by hand.
    """

    def setUp(self):
        self.ns = _updater()

    def test_an_exception_mid_drain_resumes_everything(self):
        calls = []
        boom = RuntimeError("killed mid-drain")
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_drain_within": mock.Mock(side_effect=boom)}), \
             self.assertRaises(RuntimeError):
            self.ns["_drain_and_stop"](["moonshiner-trace-continuous-aaaaaaaaaaaa"], 60)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("SIGCONT" in c for c in flat),
                        "the coordinator must be resumed when the drain dies")

    def test_a_termination_signal_resumes_before_exiting(self):
        source = (ROOT / "moonshiner.py").read_text()
        block = source[source.index("def _drain_and_stop"):source.index("def _drain_within")]
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            self.assertIn(name, block, f"{name} must not leave a queue paused")
        self.assertIn("finally:", block)


if __name__ == "__main__":
    unittest.main()

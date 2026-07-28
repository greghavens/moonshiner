"""Updating must not cost a running job.

A claimed job holds a lease and represents metered teacher and judge spend plus
one of a limited attempt allowance. Replacing the release under a running
coordinator forfeits that, so the updater drains first and refuses to proceed
if anything is still working.
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
    import os, re, subprocess, time
    namespace = {"re": re, "os": os, "subprocess": subprocess, "time": time,
                 "sys": sys, "Path": pathlib.Path}
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


class Draining(unittest.TestCase):
    def setUp(self):
        self.ns = _updater()

    def test_refuses_to_stop_while_a_job_is_running(self):
        calls = []
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_live_jobs": lambda: 3}), \
             mock.patch.object(self.ns["time"], "monotonic", side_effect=[0, 10_000]), \
             mock.patch.object(self.ns["time"], "sleep"):
            drained = self.ns["_drain_and_stop"](["moonshiner-trace-continuous-aaaaaaaaaaaa"], 1)
        self.assertFalse(drained, "must not report success while jobs run")
        flat = [" ".join(c) for c in calls]
        self.assertFalse(any("stop" in c for c in flat),
                         "no queue may be stopped while a job is running")
        self.assertTrue(any("SIGCONT" in c for c in flat),
                        "a refused drain must resume the coordinator")

    def test_stops_only_after_every_job_finishes(self):
        calls = []
        with mock.patch.object(self.ns["subprocess"], "run",
                               side_effect=lambda *a, **k: calls.append(a[0]) or mock.Mock()), \
             mock.patch.dict(self.ns, {"_live_jobs": lambda: 0}), \
             mock.patch.object(self.ns["time"], "sleep"):
            drained = self.ns["_drain_and_stop"](["moonshiner-trace-continuous-aaaaaaaaaaaa"], 60)
        self.assertTrue(drained)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("SIGSTOP" in c for c in flat), "must pause claiming first")
        self.assertTrue(any("stop" in c for c in flat), "must stop once drained")
        self.assertLess(next(i for i, c in enumerate(flat) if "SIGSTOP" in c),
                        next(i for i, c in enumerate(flat) if "stop" in c),
                        "pause must precede stop")


if __name__ == "__main__":
    unittest.main()

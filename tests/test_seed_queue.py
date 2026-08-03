"""The single seed queue honors authored and retired terminal states."""
import json
import fcntl
import pathlib
import sys
import tempfile
import unittest
import threading
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import seed_queue  # noqa: E402


class SeedQueueSelection(unittest.TestCase):
    def test_active_claims_include_only_locks_held_by_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            claims = pathlib.Path(directory)
            held_path = claims / "seed-active.lock"
            idle_path = claims / "seed-idle.lock"
            held = held_path.open("a+")
            idle_path.touch()
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.assertEqual(seed_queue.active_claim_ids(claims),
                                 {"seed-active"})
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)
                held.close()
            reader = idle_path.open("r")
            fcntl.flock(reader, fcntl.LOCK_SH | fcntl.LOCK_NB)
            try:
                self.assertEqual(seed_queue.active_claim_ids(claims), set(),
                                 "concurrent status readers are not workers")
            finally:
                fcntl.flock(reader, fcntl.LOCK_UN)
                reader.close()

    def test_retired_and_authored_seeds_are_not_requeued(self):
        with mock.patch.object(seed_queue, "documented_plan_items", return_value={
                "authored": "done", "retired": "retired", "waiting": "new"}), \
             mock.patch.object(seed_queue, "authored_ids", return_value={"authored"}), \
             mock.patch.object(seed_queue, "retired_seed_ids", return_value={"retired"}), \
             mock.patch("builtins.print") as output:
            self.assertEqual(seed_queue.main(["--dry-run", "--workers", "2"]), 0)
        self.assertIn("authored=1, retired=1, waiting=1, workers=2",
                      output.call_args.args[0])

    def test_two_workers_means_two_concurrent_seed_author_processes(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def run(*_args, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            threading.Event().wait(0.03)
            with lock:
                active -= 1
            return mock.Mock(returncode=0)

        plans = {f"seed-{index}": "brief" for index in range(4)}
        with mock.patch.object(seed_queue, "documented_plan_items", return_value=plans), \
             mock.patch.object(seed_queue, "authored_ids", return_value=set()), \
             mock.patch.object(seed_queue, "retired_seed_ids", return_value=set()), \
             mock.patch.object(seed_queue, "load_seeds", return_value=[]), \
             mock.patch.object(seed_queue, "_moonshiner", return_value="moonshiner"), \
             mock.patch.object(seed_queue, "ensure_seed_repo"), \
             mock.patch.object(seed_queue.subprocess, "run", side_effect=run):
            self.assertEqual(seed_queue.main(["--yes", "--workers", "2"]), 0)
        self.assertEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()


class PlanPriority(unittest.TestCase):
    """A plan can claim the front of the authoring queue.

    Ordering was alphabetical, so a competence needed now waited behind every
    alphabetically earlier ID — a thousand of them in this corpus.
    """

    def _plan(self, directory, name, prefix, priority=None):
        plan = {"plan": name, "id_prefix": prefix,
                "artifact_contract": "genuine_harness_task",
                "families": [{"scenario": name, "program": name,
                              "category": "feature-integration",
                              "training_tags": [name], "count": 2,
                              "template": "Author {domain} with {constraint}."}]}
        if priority is not None:
            plan["priority"] = priority
        (directory / f"{name}.json").write_text(json.dumps(plan))

    def test_a_prioritised_plan_is_authored_before_the_alphabet(self):
        import tempfile
        from seed_inventory import plan_priorities
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            self._plan(directory, "aardvark", "aaa-")
            self._plan(directory, "urgent", "zzz-", priority=100)
            priorities = plan_priorities(directory)
            planned = ["aaa-0001", "aaa-0002", "zzz-0001", "zzz-0002"]
            ordered = sorted(planned,
                             key=lambda s: (-priorities.get(s, 0), s))
            self.assertEqual(["zzz-0001", "zzz-0002", "aaa-0001", "aaa-0002"],
                             ordered)

    def test_replacement_work_has_lower_priority_than_first_authorship(self):
        import seed_inventory
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            self._plan(directory, "replacement", "legacy-", priority=100)
            replacement = {"id": "legacy-0001"}
            with mock.patch.object(seed_inventory, "PLANS", directory), \
                 mock.patch.object(seed_inventory, "select_seeds",
                                   return_value=[replacement]), \
                 mock.patch.object(seed_inventory, "synthetic_tool_contract",
                                   return_value="legacy synthetic contract"):
                priorities = seed_inventory.plan_priorities()
            self.assertEqual(priorities["legacy-0001"], -1)


class ReplacementIntake(unittest.TestCase):
    def test_replacements_are_queued_without_an_imports_directory(self):
        import seed_inventory
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            plans = root / "plans"
            plans.mkdir()
            replacement = {"id": "legacy-seed", "training_tags": ["legacy"],
                           "prompt": "Preserve this objective."}
            with mock.patch.object(seed_inventory, "PLANS", plans), \
                 mock.patch.object(seed_inventory, "STORAGE_ROOT", root / "state"), \
                 mock.patch.object(seed_inventory, "select_seeds",
                                   return_value=[replacement]), \
                 mock.patch.object(seed_inventory, "synthetic_tool_contract",
                                   return_value="legacy synthetic contract"):
                items = seed_inventory.documented_plan_items()
            self.assertIn("legacy-seed", items)
            self.assertIn("Reauthor this existing objective", items["legacy-seed"])


class TracePriorityIsSeparate(unittest.TestCase):
    """Authoring order and tracing order are different questions.

    A plan is often worth authoring ahead of the queue while its traces wait
    behind work already in flight, so one field cannot answer both.
    """

    def _write(self, directory, name, prefix, **fields):
        plan = {"plan": name, "id_prefix": prefix,
                "artifact_contract": "genuine_harness_task",
                "families": [{"scenario": name, "program": name,
                              "category": "feature-integration",
                              "training_tags": [name], "count": 2,
                              "template": "Author {domain} with {constraint}."}],
                **fields}
        (directory / f"{name}.json").write_text(json.dumps(plan))

    def test_authoring_priority_does_not_imply_tracing_priority(self):
        import tempfile
        from seed_inventory import plan_priorities, plan_trace_priorities
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            self._write(directory, "urgent", "aaa-", priority=110)
            self._write(directory, "later", "bbb-", priority=100, trace_priority=50)
            self.assertEqual({"aaa-0001": 110, "aaa-0002": 110,
                              "bbb-0001": 100, "bbb-0002": 100},
                             plan_priorities(directory))
            self.assertEqual({"bbb-0001": 50, "bbb-0002": 50},
                             plan_trace_priorities(directory),
                             "a plan authored first is not traced first by default")

    def test_the_pipeline_orders_by_trace_priority_under_explicit_entries(self):
        source = (ROOT / "src" / "trace_pipeline.py").read_text()
        block = source[source.index("queue_order = {entry"):source.index("if args.limit")]
        self.assertIn("plan_trace_priorities", block)
        self.assertLess(block.index("queue_order.get"), block.index("trace_priority.get"),
                        "an explicit queue entry must outrank a plan's priority")

"""The one seed queue must deduplicate work across coordinator processes."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import seed_queue  # noqa: E402


class SeedQueueDeduplication(unittest.TestCase):
    def test_competing_workers_author_an_id_exactly_once(self):
        authored = set()
        calls = []
        guard = threading.Lock()

        def run(command, cwd):
            with guard:
                calls.append(command)
            time.sleep(0.05)
            authored.add("seed-0001")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(seed_queue, "CLAIMS", pathlib.Path(directory)), \
             mock.patch.object(seed_queue, "authored_ids",
                               side_effect=lambda: set(authored)), \
             mock.patch.object(seed_queue, "load_seeds", return_value=[]), \
             mock.patch.object(seed_queue, "_moonshiner", return_value="moonshiner"), \
             mock.patch.object(seed_queue.subprocess, "run", side_effect=run):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _: seed_queue.author_one(
                        "seed-0001", {"seed-0001": "brief"}), range(2)))

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [("seed-0001", 0), ("seed-0001", 0)])

    def test_live_worker_decrease_waits_for_both_active_jobs(self):
        worker_config = {"workers": 2}
        calls = []
        results = []
        guard = threading.Lock()
        two_started = threading.Event()
        release_first = threading.Event()
        release_second = threading.Event()
        first_finished = threading.Event()
        reduced_limit_observed = threading.Event()

        def author_one(seed_id, _plans):
            with guard:
                calls.append(seed_id)
                if len(calls) == 2:
                    two_started.set()
            if seed_id == "seed-0001":
                release_first.wait(5)
                first_finished.set()
            elif seed_id == "seed-0002":
                release_second.wait(5)
            return seed_id, 0

        def load_config():
            if worker_config["workers"] == 1 and first_finished.is_set():
                reduced_limit_observed.set()
            return {"pipeline": {"seed": dict(worker_config)}}

        plans = {f"seed-{number:04d}": "brief" for number in range(1, 5)}
        with mock.patch.object(seed_queue, "CONFIG", {
                 "pipeline": {"seed": {"workers": 2}}}), \
             mock.patch.object(seed_queue, "load_config", side_effect=load_config), \
             mock.patch.object(seed_queue, "ensure_seed_repo"), \
             mock.patch.object(seed_queue, "documented_plan_items", return_value=plans), \
             mock.patch.object(seed_queue, "authored_ids", return_value=set()), \
             mock.patch.object(seed_queue, "retired_seed_ids", return_value=set()), \
             mock.patch.object(seed_queue, "plan_priorities", return_value={}), \
             mock.patch.object(seed_queue, "author_one", side_effect=author_one):
            coordinator = threading.Thread(
                target=lambda: results.append(seed_queue.main(["--yes"])))
            coordinator.start()
            started = two_started.wait(2)
            worker_config["workers"] = 1
            release_first.set()
            observed = reduced_limit_observed.wait(3)
            with guard:
                calls_at_reduced_boundary = list(calls)
            release_second.set()
            coordinator.join(5)

        self.assertTrue(started)
        self.assertTrue(observed)
        self.assertCountEqual(calls_at_reduced_boundary,
                              ["seed-0001", "seed-0002"])
        self.assertFalse(coordinator.is_alive())
        self.assertEqual(results, [0])


if __name__ == "__main__":
    unittest.main()

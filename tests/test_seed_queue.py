"""The single seed queue honors authored and retired terminal states."""
import pathlib
import sys
import unittest
import threading
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import seed_queue  # noqa: E402


class SeedQueueSelection(unittest.TestCase):
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
             mock.patch.object(seed_queue.subprocess, "run", side_effect=run):
            self.assertEqual(seed_queue.main(["--yes", "--workers", "2"]), 0)
        self.assertEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()

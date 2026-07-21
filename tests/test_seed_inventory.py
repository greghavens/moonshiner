"""Seed inventory counts distinguish presence from execution readiness."""
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import seed_inventory  # noqa: E402
import run_state  # noqa: E402


class SeedInventoryCounts(unittest.TestCase):
    def test_catalogued_counts_every_present_seed_while_ready_excludes_replacement(self):
        seeds = [
            {"id": "ready", "prompt": "Do the task"},
            {"id": "replace", "prompt": "Do the task", "tool_results": {"x": "fake"}},
        ]
        with mock.patch.object(seed_inventory, "select_seeds", return_value=seeds), \
             mock.patch.object(seed_inventory, "synthetic_tool_contract",
                               side_effect=[None, "embedded synthetic tool results"]):
            self.assertEqual(seed_inventory.catalogued_ids(), {"ready", "replace"})
            self.assertEqual(seed_inventory.authored_ids(), {"ready"})

    def test_latest_seed_exhaustion_is_durable_retirement(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / "ledger.sqlite3"
            db = run_state.connect(database)
            run_id = run_state.create_run(db, "seed", {}, {"max_attempts": 2},
                                          ["retired-seed"])
            run_state.start_attempt(db, run_id, "retired-seed", 1)
            run_state.finish_attempt(db, run_id, "retired-seed", 1, "retired")
            self.assertEqual(seed_inventory.retired_seed_ids(db), {"retired-seed"})
            db.close()

    def test_newer_accepted_seed_revision_clears_prior_retirement(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / "ledger.sqlite3"
            db = run_state.connect(database)
            old = run_state.create_run(db, "seed", {}, {}, ["seed"])
            run_state.start_attempt(db, old, "seed", 1)
            run_state.finish_attempt(db, old, "seed", 1, "retired")
            newer = run_state.create_run(db, "seed", {}, {}, ["seed"])
            run_state.start_attempt(db, newer, "seed", 1)
            run_state.finish_attempt(db, newer, "seed", 1, "accepted")
            self.assertEqual(seed_inventory.retired_seed_ids(db), set())
            db.close()


if __name__ == "__main__":
    unittest.main()

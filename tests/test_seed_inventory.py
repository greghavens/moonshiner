"""Seed inventory counts distinguish presence from execution readiness."""
import pathlib
import sys
import unittest
from collections import Counter
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import seed_inventory  # noqa: E402
import run_state  # noqa: E402


class SeedInventoryCounts(unittest.TestCase):
    def test_active_repo_seed_ids_are_unique_and_replaced_sources_are_preserved(self):
        seeds = seed_inventory.select_seeds()
        ids = [seed["id"] for seed in seeds]
        self.assertEqual(len(ids), len(set(ids)))
        retired = pathlib.Path(__file__).resolve().parents[1] / "tasks" / "retired-seeds"
        self.assertTrue(any(retired.glob("*.json")))

    def test_inventory_sets_load_the_seed_catalog_once(self):
        seeds = [
            {"id": "ready", "prompt": "Do the task"},
            {"id": "replace", "tool_results": {"fake": "result"}},
        ]
        with mock.patch.object(seed_inventory, "select_seeds",
                               return_value=seeds) as load, \
                mock.patch.object(seed_inventory, "synthetic_tool_contract",
                                  side_effect=lambda seed: (
                                      "synthetic" if seed["id"] == "replace" else None)):
            catalogued, ready, replacements = seed_inventory.inventory_sets()
        load.assert_called_once_with()
        self.assertEqual(catalogued, {"ready", "replace"})
        self.assertEqual(ready, {"ready"})
        self.assertEqual(replacements, {"replace"})

    def test_bundled_round_three_plan_is_exactly_1000_unique_genuine_harness_scenarios(self):
        records = seed_inventory.bundled_plan_records()
        round_three = [record for record in records
                       if record.get("plan") == "instruction-following-round-3"]
        self.assertEqual(len(round_three), 1000)
        self.assertEqual(len({record["id"] for record in round_three}), 1000)
        self.assertEqual(len({record["brief"] for record in round_three}), 1000)
        self.assertEqual({record["artifact_contract"] for record in round_three},
                         {"genuine_harness_task"})
        required = {"multi-turn-correction", "web-research-synthesis-revision",
                    "parallel-retrieval-dependent-action", "persistent-state",
                    "partial-failure-recovery", "compound-constraints"}
        self.assertEqual({record["scenario"] for record in round_three}, required)
        self.assertTrue(all("real reachable sources" in record["brief"]
                            for record in round_three
                            if record["scenario"] == "web-research-synthesis-revision"))
        self.assertEqual(Counter(record["program"] for record in round_three), {
            "Instruction following": 680,
            "Tool calling": 170,
            "Error recovery": 150,
        })
        expected_categories = {
            "multi-turn-correction": "multi-turn-state",
            "web-research-synthesis-revision": "web-research",
            "parallel-retrieval-dependent-action": "dependency-planning",
            "persistent-state": "persistent-memory",
            "partial-failure-recovery": "error-recovery",
            "compound-constraints": "long-context-composite",
        }
        prohibited = {"behavior", "bfcl", "full-distill"}
        for record in round_three:
            self.assertEqual(record["category"],
                             expected_categories[record["scenario"]])
            self.assertTrue(record["training_tags"])
            labels = {record["program"].casefold(), record["category"].casefold(),
                      *(tag.casefold() for tag in record["training_tags"])}
            self.assertTrue(prohibited.isdisjoint(labels))
            self.assertIn(f'Program: {record["program"]}.', record["brief"])
            self.assertIn(f'Category: {record["category"]}.', record["brief"])
            self.assertIn("Training tags: " + ", ".join(record["training_tags"]),
                          record["brief"])

    def test_bundled_plan_extends_documented_inventory_without_cataloguing_unwritten_seeds(self):
        records = [{"id": "planned-new", "brief": "A new scenario",
                    "artifact_contract": "genuine_harness_task"}]
        with mock.patch.object(seed_inventory, "bundled_plan_records", return_value=records), \
             mock.patch.object(seed_inventory, "select_seeds", return_value=[]), \
             mock.patch.object(pathlib.Path, "is_dir", return_value=False):
            self.assertIn("planned-new", seed_inventory.documented_plan_ids())
            self.assertNotIn("planned-new", seed_inventory.catalogued_ids())
            self.assertEqual(seed_inventory.documented_plan_items()["planned-new"],
                             "A new scenario")

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

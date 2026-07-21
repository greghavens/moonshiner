"""Seed inventory counts distinguish presence from execution readiness."""
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import seed_inventory  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()

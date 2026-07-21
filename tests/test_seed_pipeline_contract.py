"""Seed plans select one uniform genuine-harness artifact contract."""
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import seed_pipeline  # noqa: E402


class SeedPipelineContractTests(unittest.TestCase):
    def test_round_three_plan_uses_genuine_harness_directory_artifacts(self):
        record = {"artifact_contract": "genuine_harness_task"}
        with mock.patch.object(seed_pipeline, "bundled_plan_record", return_value=record):
            prompt = seed_pipeline._author_system("instruction-following-r3-0001")
        self.assertIn("task.json, files/, and reference_fix.patch", prompt)
        self.assertIn("selected unmodified agent harness", prompt)
        self.assertIn("real reachable sources", prompt)
        self.assertIn("must never be simulated", prompt)

    def test_unknown_bundled_artifact_contract_fails_instead_of_selecting_an_alternate_path(self):
        record = {"artifact_contract": "invented-contract"}
        with mock.patch.object(seed_pipeline, "bundled_plan_record", return_value=record):
            with self.assertRaisesRegex(ValueError, "unsupported seed artifact contract"):
                seed_pipeline._author_system("planned-seed")


if __name__ == "__main__":
    unittest.main()

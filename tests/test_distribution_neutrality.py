import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DistributionNeutralityTest(unittest.TestCase):
    def test_default_config_does_not_select_a_target_model_or_external_seed_repo(self):
        config = json.loads((ROOT / "config.json").read_text())
        self.assertEqual(config["teacher"]["model"], "")
        self.assertEqual(config["seed_author"]["model"], "")
        self.assertEqual(config["source"], {})

    def test_seed_sync_requires_an_explicit_source_repository(self):
        script = (ROOT / "scripts" / "update_seed_source.sh").read_text()
        self.assertIn("MOONSHINER_SEED_REPOSITORY:?", script)
        self.assertNotIn("greghavens/sol-code", script)
        service = (ROOT / "scripts" / "seed-sync.service").read_text()
        self.assertIn("EnvironmentFile=", service)
        self.assertNotIn("sol-code", service)


if __name__ == "__main__":
    unittest.main()

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DistributionNeutralityTest(unittest.TestCase):
    def test_console_and_package_metadata_versions_are_identical(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
        namespace = {}
        exec((ROOT / "src" / "moonshiner_app" / "__init__.py").read_text(),
             namespace)
        self.assertEqual(namespace["__version__"],
                         metadata["project"]["version"])

    def test_default_config_does_not_select_a_target_model_or_external_seed_repo(self):
        config = json.loads((ROOT / "config.json").read_text())
        self.assertEqual(config["teacher"]["model"], "")
        self.assertEqual(config["seed_author"]["model"], "")
        self.assertEqual(config["source"], {})

    def test_no_external_seed_source_exists(self):
        """Moonshiner is the source of Moonshiner seeds.

        Seeds are authored here and committed here. Importing them from another
        repository is not a supported shape, so the machinery for it must not
        come back.
        """
        scripts = ROOT / "scripts"
        for path in scripts.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            for banned in ("sol-code", "MOONSHINER_SEED_REPOSITORY",
                           "MOONSHINER_SEED_SOURCE",
                           "MOONSHINER_SEED_ACCEPTED_DIR"):
                self.assertNotIn(banned, text,
                                 f"{path.name} reintroduces an external seed source")
        self.assertFalse((scripts / "update_seed_source.sh").exists())

    def test_seed_sync_publishes_from_the_configured_clone(self):
        script = (ROOT / "scripts" / "sync_seeds.sh").read_text()
        self.assertIn("MOONSHINER_SEED_REPO_PATH", script)
        # The gates that make an automated commit trustworthy.
        self.assertIn("scripts/check.sh", script)
        self.assertIn("refusing to sync", script)

    def test_seed_sync_unit_runs_the_installed_release(self):
        service = (ROOT / "scripts" / "seed-sync.service").read_text()
        self.assertIn("moonshiner seed-sync run", service)
        # A version-pinned wheel path would break on every upgrade.
        self.assertNotIn("site-packages", service)
        self.assertNotIn("ExecStartPre", service)


if __name__ == "__main__":
    unittest.main()

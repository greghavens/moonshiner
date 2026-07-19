"""Human authentication commands resolve providers, not harness names."""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import control_cli


class CredentialTargets(unittest.TestCase):
    CONFIG = {"runtimes": {
        "pi-openrouter": {"provider": "openrouter", "display_provider": "OpenRouter"},
        "codex": {"cli": "codex"},
    }}

    def test_provider_name_resolves_pi_profile(self):
        with mock.patch.object(control_cli, "CONFIG", self.CONFIG):
            provider, config = control_cli._credential_target("openrouter")
        self.assertEqual(provider, "openrouter")
        self.assertEqual(config["display_provider"], "OpenRouter")

    def test_cli_harness_remains_a_compatibility_alias(self):
        with mock.patch.object(control_cli, "CONFIG", self.CONFIG):
            provider, _ = control_cli._credential_target("codex")
        self.assertEqual(provider, "codex")


if __name__ == "__main__":
    unittest.main()

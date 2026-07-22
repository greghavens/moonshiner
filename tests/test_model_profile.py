import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import model_profile


class UnknownModelProfile(unittest.TestCase):
    def test_unknown_model_needs_no_source_change(self):
        profile = model_profile.build("acme/orion-7b-v2")
        self.assertEqual(profile["display_name"], "Orion 7B V2")
        self.assertEqual(profile["banner_source"],
                         "assets/moonshiner-dataset-banner.png")

    def test_explicit_provider_alias_attests_without_fuzzy_matching(self):
        self.assertTrue(model_profile.matches(
            "acme/orion-7b", "orion-7b-2026-07-21",
            ["orion-7b-2026-07-21"]))
        self.assertFalse(model_profile.matches(
            "acme/orion-7b", "different-model",
            ["orion-7b-2026-07-21"]))


if __name__ == "__main__":
    unittest.main()

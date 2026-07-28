"""Authoring one seed is a single piece of paid work; it must not be wasted."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import seed_pipeline  # noqa: E402


class AuthorIsToldTheId(unittest.TestCase):
    """The author must not have to guess the id from its directory name.

    It was given the brief and the workspace, and left to infer the id. When
    it inferred a descriptive one instead, the finished seed was rejected on
    load and every minute of metered authoring was discarded.
    """

    def test_the_required_id_is_stated_in_the_author_system_prompt(self):
        system = seed_pipeline._author_system("vcf90-0002")
        self.assertIn('"id" must be exactly "vcf90-0002"', system)

    def test_a_reauthored_seed_is_told_the_same(self):
        system = seed_pipeline._author_system("behavior-x-0001",
                                              replace_synthetic=True)
        self.assertIn('"id" must be exactly "behavior-x-0001"', system)

    def test_the_contract_the_author_works_to_is_otherwise_unchanged(self):
        system = seed_pipeline._author_system("vcf90-0002")
        self.assertIn("Create exactly task.json, files/, and reference_fix.patch",
                      system)
        self.assertIn("never be simulated", system)


if __name__ == "__main__":
    unittest.main()

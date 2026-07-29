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


class TheJudgeCorrectsAndTheSeedMovesOn(unittest.TestCase):
    """Authoring is: author, judge, judge fixes, move on.

    The pipeline re-ran validation after the judge had edited and re-verified
    the candidate, and failed the seed on that second opinion. It discarded
    work the judge had already fixed, including faults no edit to a seed can
    fix — a runtime writing cache files into the sandbox HOME, for one.
    """

    def test_the_verdict_alone_decides(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        decision = source[source.index("            accepted = "):]
        decision = decision[:decision.index("\n")]
        self.assertEqual("accepted = verdict_clear", decision.strip())

    def test_validation_still_runs_for_the_record(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        self.assertIn("final_report = validate_report(seed)", source,
                      "the report is still produced, it just does not veto")

if __name__ == "__main__":
    unittest.main()

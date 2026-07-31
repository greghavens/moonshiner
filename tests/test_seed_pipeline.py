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


class ASeedIsNeverDiscarded(unittest.TestCase):
    """Authoring is paid for. Judging is paid for. Discarding buys both again.

    The pipeline returned 1 and left the candidate in a scratch directory
    whenever the judge had not cleared it, so every unresolved seed was
    re-authored from nothing on the next pass and charged twice.
    """

    def test_the_candidate_is_promoted_whatever_the_verdict(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        body = source[source.index("if not accepted:"):]
        body = body[:body.index("shutil.copytree(candidate, destination)")]
        self.assertNotIn("return 1", body,
                         "an unresolved seed must not abandon the paid work")

    def test_the_promotion_is_unconditional(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        promote = source.index("shutil.copytree(candidate, destination)")
        guard_before = source[:promote].rstrip().splitlines()[-1]
        self.assertNotIn("if ", guard_before,
                         "promotion must not sit behind an acceptance test")



class AnAlternateTaskSchemaIsTranslated(unittest.TestCase):
    """A seed must never reach the corpus in a shape the audit calls partial.

    vcf91-0109 was authored with `instruction` and a `verification` object
    rather than `prompt` and `verify_cmd`. Discarding it is not an option, so
    the difference is translated before the judge ever sees the candidate.
    """

    def test_instruction_and_verification_become_prompt_and_verify_cmd(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            (directory / "task.json").write_text(json.dumps({
                "id": "vcf91-0109", "category": "project-integration",
                "instruction": "Implement the vcenter package.",
                "verification": {"command": "bash .moonshiner/verify/verify.sh",
                                 "timeout_seconds": 120,
                                 "protected_paths": [".moonshiner/verify"]}}))
            seed_pipeline._normalise_task(directory)
            data = json.loads((directory / "task.json").read_text())
        self.assertEqual("Implement the vcenter package.", data["prompt"])
        self.assertEqual("bash .moonshiner/verify/verify.sh", data["verify_cmd"])
        self.assertEqual(120, data["verify_timeout"])
        self.assertEqual([".moonshiner/verify"], data["test_files"])
        self.assertNotIn("instruction", data)

    def test_a_task_already_in_the_expected_shape_is_untouched(self):
        import json, tempfile
        original = {"id": "vcf91-0009", "prompt": "Do the thing",
                    "verify_cmd": "pwsh -File t.ps1"}
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            (directory / "task.json").write_text(json.dumps(original))
            seed_pipeline._normalise_task(directory)
            self.assertEqual(original,
                             json.loads((directory / "task.json").read_text()))


if __name__ == "__main__":
    unittest.main()

"""Authoring one seed is a single piece of paid work; it must not be wasted."""
import json
import pathlib
import sys
import tempfile
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import seed_pipeline  # noqa: E402
import common  # noqa: E402


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

    def test_author_and_reauthor_receive_self_containment_and_prompt_purity(self):
        for replace_synthetic in (False, True):
            with self.subTest(replace_synthetic=replace_synthetic):
                system = seed_pipeline._author_system(
                    "security-r1-0001", replace_synthetic=replace_synthetic)
                self.assertIn("self-contained", system)
                self.assertIn("sibling repositories", system)
                self.assertIn("prompt must contain only the end-user task", system)
                self.assertIn("authoring instructions", system)

    def test_author_and_reauthor_cannot_invent_product_requirements(self):
        for replace_synthetic in (False, True):
            with self.subTest(replace_synthetic=replace_synthetic):
                system = seed_pipeline._author_system(
                    "security-r1-0001", replace_synthetic=replace_synthetic)
                self.assertIn("requested seed objective and constraints", system)
                self.assertIn("Do not broaden them", system)
                self.assertIn("approval or eligibility gate", system)
                self.assertIn("unless the", system)
                self.assertIn("explicitly requests it", system)


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

    def test_judge_is_authorized_to_repair_without_approval(self):
        prompt = seed_pipeline._review_prompt(
            {"id": "vcf91-0004"}, {"passed": False})
        self.assertIn("authorized to make every necessary in-scope repair", prompt)
        self.assertIn("without asking for human approval", prompt)
        self.assertIn("Do not reject or defer a seed merely because it requires edits", prompt)
        self.assertIn("Use needs_human only", prompt)

    def test_judge_independently_enforces_seed_artifact_integrity(self):
        prompt = seed_pipeline._review_prompt(
            {"id": "security-r1-0001"}, {"passed": False})
        self.assertIn("Independently enforce the final artifact contract", prompt)
        self.assertIn("must be self-contained", prompt)
        self.assertIn("prompt must contain only the end-user task", prompt)
        self.assertIn("unmodified harness must execute every tool call", prompt)
        self.assertIn("Web research must use real reachable sources", prompt)
        self.assertIn("grade the resulting environment or artifacts", prompt)

    def test_judge_cannot_invent_product_requirements_while_repairing(self):
        prompt = seed_pipeline._review_prompt(
            {"id": "security-r1-0001"}, {"passed": False})
        self.assertIn("Judge and repair only against the requested", prompt)
        self.assertIn("Do not broaden them", prompt)
        self.assertIn("approval or eligibility gate", prompt)
        self.assertIn("unless the seed explicitly requests it", prompt)

    def test_seed_judge_is_edit_enabled(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        call = source[source.index("review = judge.run_review("):]
        call = call[:call.index("# Reload judge edits")]
        self.assertIn("read_only=False", call)


class ASeedIsNeverDiscarded(unittest.TestCase):
    """Authoring is paid for. Judging is paid for. Discarding buys both again.

    The pipeline returned 1 and left the candidate in a scratch directory
    whenever the judge had not cleared it, so every unresolved seed was
    re-authored from nothing on the next pass and charged twice.
    """

    def test_the_candidate_is_promoted_whatever_the_verdict(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        body = source[source.index("if not accepted:"):]
        body = body[:body.index("_promote_candidate(candidate, destination)")]
        self.assertNotIn("return 1", body,
                         "an unresolved seed must not abandon the paid work")

    def test_the_promotion_is_unconditional(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        promote = source.index("_promote_candidate(candidate, destination)")
        guard_before = source[:promote].rstrip().splitlines()[-1]
        self.assertNotIn("if ", guard_before,
                         "promotion must not sit behind an acceptance test")

    def test_a_completed_retained_author_workspace_is_reused(self):
        seed_id = f"unit-retained-{uuid.uuid4().hex}"
        retained = seed_pipeline.WORKSPACES / f"author-{seed_id}-complete"
        incomplete = seed_pipeline.WORKSPACES / f"author-{seed_id}-newer"
        try:
            retained.mkdir(parents=True)
            (retained / "task.json").write_text(
                json.dumps({"id": seed_id, "prompt": "implement"}) + "\n")
            (retained / "reference_fix.patch").write_text("patch\n")
            (retained / "files").mkdir()
            incomplete.mkdir()
            selected = seed_pipeline._latest_preserved_candidate(seed_id)
        finally:
            common.remove_workspace(retained)
            common.remove_workspace(incomplete)
        self.assertEqual(retained, selected)

    def test_real_promotion_excludes_runtime_state_and_keeps_seed_artifacts(self):
        root = seed_pipeline.WORKSPACES / f"promotion-{uuid.uuid4().hex}"
        candidate = root / "candidate"
        destination = root / "promoted"
        try:
            (candidate / "files").mkdir(parents=True)
            task = b'{"id":"promotion-contract","prompt":"implement"}\n'
            payload = b"seed payload\n"
            patch = b"--- /dev/null\n+++ answer.txt\n@@ -0,0 +1 @@\n+answer\n"
            (candidate / "task.json").write_bytes(task)
            (candidate / "files" / "answer.txt").write_bytes(payload)
            (candidate / "reference_fix.patch").write_bytes(patch)
            runtime_skill = (candidate / ".sandbox-home" / "codex" /
                             "skills" / ".system" / "openai-docs")
            runtime_skill.mkdir(parents=True)
            (runtime_skill / "SKILL.md").write_text("runtime state\n")
            volatile = candidate / ".sandbox-home" / "tmp" / "vanished-module"
            volatile.parent.mkdir(parents=True, exist_ok=True)
            volatile.symlink_to(candidate / "module-that-no-longer-exists")
            for name in (".git", ".codex", ".agents", ".toolchain",
                         "__pycache__"):
                (candidate / name).mkdir()
                (candidate / name / "runtime-state").write_text(
                    "runtime state\n")
            (candidate / "task.json.authored").write_text("runtime state\n")

            seed_pipeline._promote_candidate(candidate, destination)

            self.assertEqual(task, (destination / "task.json").read_bytes())
            self.assertEqual(
                payload, (destination / "files" / "answer.txt").read_bytes())
            self.assertEqual(
                patch, (destination / "reference_fix.patch").read_bytes())
            self.assertEqual(
                {"task.json", "files", "reference_fix.patch"},
                {path.name for path in destination.iterdir()})
            self.assertEqual([], [
                path for path in destination.rglob("SKILL.md")
                if not path.is_relative_to(destination / "files")])
        finally:
            common.remove_workspace(root)

    def test_fresh_author_workspace_uses_the_durable_promotion_boundary(self):
        source = (ROOT / "src" / "seed_pipeline.py").read_text()
        fresh_author = source[source.index("authored = author.run_trace("):]
        fresh_author = fresh_author[:fresh_author.index("_normalise_task(candidate)")]
        self.assertIn("_promote_candidate(workspace, candidate)", fresh_author)
        self.assertNotIn("shutil.copytree(workspace", fresh_author)


class InfrastructureFailuresReachTheJudge(unittest.TestCase):
    def test_judge_repairs_a_preserved_candidate_before_preflight_retries(self):
        author = mock.Mock(name="author")
        author.name = "codex"
        author.role = {"model": "gpt-5.6-sol", "reasoning": "xhigh"}
        judge = mock.Mock(name="judge")
        judge.name = "codex"
        judge.role = {"model": "gpt-5.6-sol", "reasoning": "xhigh"}

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspaces = root / "workspaces"; workspaces.mkdir()
            candidates = root / "candidates"
            preserved = candidates / "old-run" / "vcf91-0004"
            preserved.mkdir(parents=True)
            (preserved / "task.json").write_text(
                '{"id":"vcf91-0004","prompt":"fix it"}\n')
            seeds = root / "seeds"; seeds.mkdir()
            traces = root / "traces"
            judge.run_review.side_effect = lambda *_args, **_kwargs: (
                SimpleNamespace(verdict={
                    "verdict": "accept",
                    "seed_reviews": [{"seed_id": "vcf91-0004",
                                      "status": "accept"}],
                }))
            with mock.patch.object(seed_pipeline, "WORKSPACES", workspaces), \
                 mock.patch.object(seed_pipeline, "CANDIDATES", candidates), \
                 mock.patch.object(seed_pipeline, "SEEDS_DIR", seeds), \
                 mock.patch.object(seed_pipeline, "TRACES", traces), \
                 mock.patch.object(seed_pipeline, "get_seed_author",
                                   return_value=author), \
                 mock.patch.object(seed_pipeline, "get_seed_judge",
                                   return_value=judge), \
                 mock.patch.object(seed_pipeline, "connect",
                                   return_value=mock.Mock()), \
                 mock.patch.object(seed_pipeline, "create_run",
                                   return_value="seed-run"), \
                 mock.patch.object(seed_pipeline, "set_run_status") as status, \
                 mock.patch.object(seed_pipeline, "start_attempt") as attempt, \
                 mock.patch.object(seed_pipeline, "preflight_seed_environment",
                                   side_effect=[
                                       (False, "bad module version"),
                                       (True, "module available")]), \
                 mock.patch.object(seed_pipeline, "validate_report",
                                   return_value={"passed": True,
                                                 "failures": []}), \
                 mock.patch.object(seed_pipeline, "audit_seed",
                                   return_value=None), \
                 mock.patch("corpus.write_catalog"):
                result = seed_pipeline.main([
                    "--id", "vcf91-0004", "--brief", "VCF seed", "--yes"])
            self.assertEqual(result, 0)
            self.assertTrue((seeds / "vcf91-0004" / "task.json").is_file())
        author.run_trace.assert_not_called()
        judge.run_review.assert_called_once()
        report = judge.run_review.call_args.args[0]
        self.assertIn("environment preflight: bad module version", report)
        attempt.assert_called_once()
        status.assert_called_with(mock.ANY, "seed-run", "complete")

    def test_unrepaired_preflight_failure_stops_without_promotion(self):
        author = mock.Mock(name="author")
        author.name = "claude-code"
        author.role = {"model": "claude-opus-5", "reasoning": "xhigh"}
        judge = mock.Mock(name="judge")
        judge.name = "codex"
        judge.role = {"model": "gpt-5.6-sol", "reasoning": "xhigh"}
        judge.run_review.return_value = SimpleNamespace(verdict={
            "verdict": "accept",
            "seed_reviews": [{"seed_id": "vcf91-0004",
                              "status": "accept"}],
        })

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            workspaces = root / "workspaces"; workspaces.mkdir()
            candidates = root / "candidates"
            preserved = candidates / "old-run" / "vcf91-0004"
            preserved.mkdir(parents=True)
            (preserved / "task.json").write_text(
                '{"id":"vcf91-0004","prompt":"fix it"}\n')
            seeds = root / "seeds"; seeds.mkdir()
            traces = root / "traces"
            with mock.patch.object(seed_pipeline, "WORKSPACES", workspaces), \
                 mock.patch.object(seed_pipeline, "CANDIDATES", candidates), \
                 mock.patch.object(seed_pipeline, "SEEDS_DIR", seeds), \
                 mock.patch.object(seed_pipeline, "TRACES", traces), \
                 mock.patch.object(seed_pipeline, "get_seed_author",
                                   return_value=author), \
                 mock.patch.object(seed_pipeline, "get_seed_judge",
                                   return_value=judge), \
                 mock.patch.object(seed_pipeline, "connect",
                                   return_value=mock.Mock()), \
                 mock.patch.object(seed_pipeline, "create_run",
                                   return_value="seed-run"), \
                 mock.patch.object(seed_pipeline, "set_run_status") as status, \
                 mock.patch.object(seed_pipeline, "preflight_seed_environment",
                                   side_effect=[
                                       (False, "bad module version"),
                                       (False, "bad module version")]), \
                 mock.patch.object(seed_pipeline, "audit_seed",
                                   return_value=None):
                result = seed_pipeline.main([
                    "--id", "vcf91-0004", "--brief", "VCF seed",
                    "--max-attempts", "1", "--yes"])
            self.assertEqual(result, seed_pipeline.INFRASTRUCTURE_EXIT)
            self.assertFalse((seeds / "vcf91-0004").exists())
            self.assertTrue((candidates / "seed-run" / "vcf91-0004" /
                             "task.json").is_file())
        author.run_trace.assert_not_called()
        judge.run_review.assert_called_once()
        status.assert_called_with(mock.ANY, "seed-run", "stopped",
                                  "bad module version")



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

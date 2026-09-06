"""The shared agent rules are one policy, applied identically by every runtime.

Moonshiner's invariant is "one pipeline, one code path for all models". The
rules a trace is judged against are part of that code path: an adapter that
quietly runs the author without them produces work the judge is guaranteed to
reject. These tests hold the policy in one place and hold every harness to it.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from runtimes import base  # noqa: E402
from runtimes.base import CODING_GUIDANCE, Runtime  # noqa: E402


class _StubRuntime(Runtime):
    """Minimal concrete Runtime: policy lives in the base class, not here."""

    name = "stub"

    def preflight(self, require_auth: bool = False) -> None:
        return None

    def run_trace(self, seed, workspace, *, out_dir, system_prompt, prompt,
                  interaction=None, security=False, tools=None):
        raise NotImplementedError

    def run_review(self, instruction, workspace, *, out_dir, schema=None,
                   read_only=True):
        raise NotImplementedError

    @staticmethod
    def parse_stream(path, workspace):
        raise NotImplementedError


def _runtime(trace_config: dict) -> _StubRuntime:
    return _StubRuntime({"pipeline": {"trace": trace_config}},
                        {"model": "vendor/model"})


class CodingGuidancePolicy(unittest.TestCase):
    def test_selected_for_a_coding_program(self):
        runtime = _runtime({"coding_system_prompt_append": True})
        self.assertEqual(runtime.coding_guidance({"_catalog_program": "Building"}),
                         CODING_GUIDANCE)

    def test_not_selected_for_a_non_coding_program(self):
        runtime = _runtime({"coding_system_prompt_append": True})
        self.assertIsNone(
            runtime.coding_guidance({"_catalog_program": "Instruction following"}))

    def test_opt_out_disables_it(self):
        runtime = _runtime({"coding_system_prompt_append": False})
        self.assertIsNone(runtime.coding_guidance({"_catalog_program": "Building"}))

    def test_configured_programs_override_the_default_set(self):
        runtime = _runtime({"coding_system_prompt_programs": ["Custom program"]})
        self.assertEqual(
            runtime.coding_guidance({"_catalog_program": "Custom program"}),
            CODING_GUIDANCE)
        self.assertIsNone(runtime.coding_guidance({"_catalog_program": "Building"}))

    def test_no_seed_means_no_guidance(self):
        """Availability probes and judge sessions pass no seed."""
        self.assertIsNone(_runtime({}).coding_guidance(None))

    def test_rules_the_judge_grades_against_are_present(self):
        """A silently empty guidance file would disarm every rule at once."""
        lowered = CODING_GUIDANCE.lower()
        for rule in ("git", "commit", "uncommitted", "/tmp", "install"):
            self.assertIn(rule, lowered)


class PolicyLivesInExactlyOnePlace(unittest.TestCase):
    """No adapter may carry its own copy of the rules or of the opt-in."""

    def test_no_runtime_adapter_defines_its_own_policy(self):
        adapters = (pathlib.Path(base.__file__).parent).glob("*.py")
        offenders = []
        for path in adapters:
            if path.name in {"base.py", "__init__.py"}:
                continue
            text = path.read_text()
            if ("CODING_GUIDANCE" in text or "CODING_PROGRAMS" in text
                    or "coding_system_prompt" in text):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "agent-rule policy must live only in "
                                        "runtimes/base.py")

    def test_every_teacher_adapter_consumes_its_system_prompt(self):
        """run_trace receives system_prompt; dropping it disarms the rules."""
        directory = pathlib.Path(base.__file__).parent
        for name in ("pi.py", "opencode.py", "codex.py", "claude_code.py",
                     "vllm.py"):
            with self.subTest(adapter=name):
                text = (directory / name).read_text()
                body = text.split("def run_trace", 1)[1]
                self.assertIn("system_prompt", body,
                              f"{name} accepts system_prompt but never uses it")


if __name__ == "__main__":
    unittest.main()

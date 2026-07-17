"""Security distiller: the offline, model-free logic gates.

The deterministic graders — the structured CWE/OWASP label gate and the
path/line recall oracle — plus teacher-prompt composition are pure and run in
every ``check.sh``. The corpus-inventory checks need a hydrated
``security/catalog`` (imported from ../fable-secure, gitignored) and skip
cleanly when it is absent.
"""
import json
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from generate_security_traces import (  # noqa: E402
    structured_reference_gate, teacher_prompt, verify_repo_findings)

_CATALOG = _ROOT / "security" / "catalog"


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class ReferenceGateTests(unittest.TestCase):
    """The structured label gate is pure; exercise it without the corpus."""

    def test_classification_requires_well_formed_labels_not_exact_mapping(self):
        case = {"meta": {"task": "classify"}}
        reference = {"expected": {
            "task": "classify", "cwe": ["CWE-79"],
            "owasp": ["A03:2021-Injection"],
        }}
        self.assertTrue(structured_reference_gate(
            case, reference, "CWE-79; A03:2021-Injection")["passed"])
        alternate = structured_reference_gate(
            case, reference, "CWE-80; A03:2021-Injection")
        self.assertTrue(alternate["passed"])
        self.assertIn("CWE-79", alternate["exact_keyed_labels_missing"])
        self.assertFalse(
            structured_reference_gate(case, reference, "This is injection.")["passed"])

    def test_non_classification_case_is_not_shape_gated(self):
        case = {"meta": {"task": "explain"}}
        reference = {"expected": {"task": "explain", "cwe": [], "owasp": []}}
        self.assertTrue(structured_reference_gate(case, reference, "prose only")["passed"])


class TeacherPromptTests(unittest.TestCase):
    """Prompt composition is pure and must never leak a held-out answer."""

    CASE = {
        "id": "sec-answer-x",
        "system": "You are a defensive security reviewer.",
        "prompt": "Classify this snippet.",
        "clone_dir": None,
    }

    def test_prompt_states_the_defensive_contract(self):
        composed = teacher_prompt(self.CASE)
        self.assertIn("defensive-security work trace", composed)
        self.assertIn("Do not create a weaponized exploit", composed)
        self.assertIn(self.CASE["system"], composed)
        self.assertIn(self.CASE["prompt"], composed)

    def test_prompt_never_carries_a_reference_answer(self):
        composed = teacher_prompt(self.CASE)
        self.assertNotIn("CORRECT REFERENCE ANSWER", composed)
        self.assertNotIn("reference_answer", composed)

    def test_repo_case_gets_the_repository_note(self):
        composed = teacher_prompt({**self.CASE, "clone_dir": "some-repo"})
        self.assertIn("authorized repository", composed)


class RepoVerifierTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"findings": [
            {"id": "a", "paths": ["src/a.py"], "lines": [10]},
            {"id": "b", "paths": ["src/b.py"], "lines": [20]},
            {"id": "c", "paths": ["src/c.py"], "lines": [30]},
        ]}
        self.thresholds = {
            "recall_min": 0.6, "precision_min": 0.0,
            "spray_cap": 4.0, "line_window": 2,
        }

    def test_location_match_passes(self):
        findings = [
            {"file": "src/a.py", "line": 11},
            {"location": "src/b.py:20"},
        ]
        verdict = verify_repo_findings(self.expected, findings, self.thresholds)
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["recall"], 0.6667)

    def test_wrong_lines_and_spray_fail(self):
        wrong = [{"file": "src/a.py", "line": 100}]
        self.assertFalse(verify_repo_findings(
            self.expected, wrong, self.thresholds)["passed"])
        spray = ([{"file": "src/a.py", "line": 10},
                  {"file": "src/b.py", "line": 20}]
                 + [{"file": f"noise/{i}.py", "line": 1} for i in range(20)])
        verdict = verify_repo_findings(self.expected, spray, self.thresholds)
        self.assertFalse(verdict["passed"])
        self.assertGreater(verdict["spray_ratio"], 4.0)

    def test_empty_findings_never_pass(self):
        self.assertFalse(verify_repo_findings(self.expected, [], self.thresholds)["passed"])


@unittest.skipUnless((_CATALOG / "cases.jsonl").exists(),
                     "security catalog not hydrated (import from ../fable-secure)")
class SecurityCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = load_jsonl(_CATALOG / "cases.jsonl")
        cls.references = load_jsonl(_ROOT / "security/keys/references.jsonl")
        cls.reviews = load_jsonl(_CATALOG / "repo_reviews.jsonl")
        cls.repo_keys = load_jsonl(_ROOT / "security/keys/repo_expected.jsonl")

    def test_complete_unique_inventory(self):
        self.assertEqual(len(self.cases), 2391)
        self.assertEqual(len(self.reviews), 18)
        ids = [row["id"] for row in self.cases + self.reviews]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({row["id"] for row in self.cases},
                         {row["id"] for row in self.references})
        self.assertEqual({row["id"] for row in self.reviews},
                         {row["id"] for row in self.repo_keys})
        self.assertEqual(sum(len(row["expected"]["findings"])
                             for row in self.repo_keys), 196)

    def test_teacher_catalog_has_no_reference_field(self):
        for case in self.cases + self.reviews:
            self.assertNotIn("reference_answer", case)
            self.assertNotIn("expected", case)
            composed = teacher_prompt(case)
            self.assertNotIn("CORRECT REFERENCE ANSWER", composed)


if __name__ == "__main__":
    unittest.main()

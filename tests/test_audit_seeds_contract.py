"""One audit over the whole corpus, keyed on what each seed declares."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import audit_seeds  # noqa: E402

WORLDS = {"calendar": {"tools": [{"name": "list_events"}, {"name": "add_event"}]}}


def _seed(root: pathlib.Path, name: str, task: dict) -> pathlib.Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "task.json").write_text(json.dumps(task))
    return directory


class DeclaredContract(unittest.TestCase):
    """Seeds differ in what they declare, never in what they are."""

    def test_minimal_seed_is_complete(self):
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {"id": "s", "category": "debug",
                                                "prompt": "p"})
            self.assertIsNone(audit_seeds.check(d, WORLDS))

    def test_domain_label_alone_is_not_a_tool_contract(self):
        """``world`` is metadata; code seeds carry it without ``expected``."""
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {
                "id": "s", "category": "debug", "prompt": "p",
                "world": "calendar"})
            self.assertIsNone(audit_seeds.check(d, WORLDS))

    def test_declared_expected_must_name_a_known_world(self):
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {
                "id": "s", "category": "debug", "prompt": "p",
                "world": "nowhere", "available_tools": [],
                "expected": {"stages": [], "forbidden_tools": []}})
            self.assertIn("unknown world", audit_seeds.check(d, WORLDS) or "")

    def test_expected_tool_must_be_available_in_its_world(self):
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {
                "id": "s", "category": "debug", "prompt": "p",
                "world": "calendar", "available_tools": ["list_events"],
                "expected": {"forbidden_tools": [], "stages": [
                    {"parallel": False, "calls": [
                        {"tool": "add_event", "arguments": {}}]}]}})
            self.assertIn("expected unavailable tools",
                          audit_seeds.check(d, WORLDS) or "")

    def test_expected_tool_may_not_also_be_forbidden(self):
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {
                "id": "s", "category": "debug", "prompt": "p",
                "world": "calendar", "available_tools": ["list_events"],
                "expected": {"forbidden_tools": ["list_events"], "stages": [
                    {"parallel": False, "calls": [
                        {"tool": "list_events", "arguments": {}}]}]}})
            self.assertIn("expected/forbidden conflict",
                          audit_seeds.check(d, WORLDS) or "")

    def test_parallel_stage_needs_two_calls(self):
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {
                "id": "s", "category": "debug", "prompt": "p",
                "world": "calendar", "available_tools": ["list_events"],
                "expected": {"forbidden_tools": [], "stages": [
                    {"parallel": True, "calls": [
                        {"tool": "list_events", "arguments": {}}]}]}})
            self.assertIn("fewer than two calls", audit_seeds.check(d, WORLDS) or "")

    def test_declared_test_files_must_ship(self):
        with tempfile.TemporaryDirectory() as name:
            d = _seed(pathlib.Path(name), "s", {
                "id": "s", "category": "debug", "prompt": "p",
                "test_files": ["t.py"]})
            self.assertEqual(audit_seeds.check(d, WORLDS), "no files/")


class NoSeparateBehaviorAudit(unittest.TestCase):
    def test_the_split_audit_is_gone(self):
        """A seed is a seed; there is no second, name-globbed audit."""
        self.assertFalse((ROOT / "scripts" / "audit_behavior_seeds.py").exists())
        self.assertNotIn("audit_behavior_seeds",
                         (ROOT / "scripts" / "check.sh").read_text())


if __name__ == "__main__":
    unittest.main()

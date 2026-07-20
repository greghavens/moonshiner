"""Installed entry-point commands that must work before project setup."""
import unittest
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from moonshiner_app.cli import _is_read_only


class ReadOnlyCommands(unittest.TestCase):
    def test_corpus_discovery_does_not_require_project_confirmation(self):
        for action in ("status", "verify", "list", "catalog", "manifest"):
            self.assertTrue(_is_read_only(["seeds", action]))

    def test_operational_commands_still_establish_a_project(self):
        for argv in (["run"], ["seed", "run"], ["publish"], ["dataset", "build"]):
            self.assertFalse(_is_read_only(argv))


if __name__ == "__main__":
    unittest.main()

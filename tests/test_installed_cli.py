"""Installed entry-point commands that must work before project setup."""
import unittest
import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from moonshiner_app.cli import _is_read_only, _run_application, install_corpus


def _bundle(root: pathlib.Path, verifier: str) -> pathlib.Path:
    bundle = root / "bundle"
    seed = bundle / "tasks" / "seeds" / "go-csvlimits"
    seed.mkdir(parents=True)
    (seed / "verify.py").write_text(verifier)
    (bundle / "corpus-version.json").write_text('{"version": "2026.07.21.1"}\n')
    return bundle


class CorpusDelivery(unittest.TestCase):
    """A seed corrected in a release has to reach the projects using it.

    Projects that author seeds keep working from their active corpus, so
    without this the corrected seed ships in every wheel and is used by none.
    """

    def test_a_new_release_replaces_a_seed_it_corrected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            active = root / "active"
            install_corpus(_bundle(root, "old"), active, release="0.6.5")
            self.assertEqual("old", (active / "tasks" / "seeds" / "go-csvlimits"
                                     / "verify.py").read_text())

            mine = active / "tasks" / "seeds" / "authored-here"
            mine.mkdir(parents=True)
            (mine / "task.json").write_text("{}")

            install_corpus(_bundle(root / "next", "corrected"), active,
                           release="0.6.6")
            self.assertEqual("corrected",
                             (active / "tasks" / "seeds" / "go-csvlimits"
                              / "verify.py").read_text())
            self.assertTrue((mine / "task.json").is_file(),
                            "a seed authored here must survive the update")

    def test_a_read_only_fixture_does_not_stop_the_update(self):
        """Seeds ship protected verifiers, keys and fixture databases 0444.

        A copy onto one of those raises PermissionError, and that aborted the
        whole start-up: the project's queues never ran.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            active = root / "active"
            install_corpus(_bundle(root, "old"), active, release="0.6.6")
            protected = active / "tasks" / "seeds" / "go-csvlimits" / "verify.py"
            protected.chmod(0o444)
            install_corpus(_bundle(root / "next", "corrected"), active,
                           release="0.6.7")
            self.assertEqual("corrected", protected.read_text())

    def test_the_same_release_leaves_the_corpus_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            active = root / "active"
            install_corpus(_bundle(root, "shipped"), active, release="0.6.6")
            seed = active / "tasks" / "seeds" / "go-csvlimits" / "verify.py"
            seed.write_text("locally patched")
            install_corpus(_bundle(root / "again", "shipped"), active,
                           release="0.6.6")
            self.assertEqual("locally patched", seed.read_text())


class ReadOnlyCommands(unittest.TestCase):
    def test_keyboard_interrupt_exits_without_traceback(self):
        with mock.patch("builtins.print") as output:
            self.assertEqual(
                _run_application(mock.Mock(side_effect=KeyboardInterrupt)), 130)
        output.assert_called_once_with("Exiting.")

    def test_corpus_discovery_does_not_require_project_confirmation(self):
        for action in ("status", "verify", "list", "catalog", "manifest"):
            self.assertTrue(_is_read_only(["seeds", action]))

    def test_operational_commands_still_establish_a_project(self):
        for argv in (["run"], ["seed", "run"], ["publish"], ["dataset", "build"]):
            self.assertFalse(_is_read_only(argv))


if __name__ == "__main__":
    unittest.main()

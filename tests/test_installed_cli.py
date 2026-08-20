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

    def test_a_seed_whose_files_moved_does_not_arrive_twice(self):
        """A release may move a seed's payload; the old paths must not linger.

        Copying over what is already there left both layouts in place, so the
        seed materialized with two copies of itself and its verifier read the
        stale one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            active = root / "active"
            install_corpus(_bundle(root, "old"), active, release="0.6.6")

            moved = _bundle(root / "next", "corrected")
            seed = moved / "tasks" / "seeds" / "go-csvlimits"
            (seed / "verify.py").unlink()
            (seed / "files").mkdir()
            (seed / "files" / "verify.py").write_text("corrected")
            install_corpus(moved, active, release="0.6.7")

            installed = active / "tasks" / "seeds" / "go-csvlimits"
            self.assertEqual("corrected", (installed / "files"
                                           / "verify.py").read_text())
            self.assertFalse((installed / "verify.py").exists(),
                             "the layout the release replaced must be gone")

    def test_the_bytecode_installing_the_package_made_is_not_delivered(self):
        """`pip install` byte-compiles the corpus riding along as package data.

        None of it is seed content: delivered, it is materialized into every
        workspace and fingerprinted as part of the seed it sits in.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bundle = _bundle(root, "shipped")
            cache = bundle / "tasks" / "seeds" / "go-csvlimits" / "__pycache__"
            cache.mkdir()
            (cache / "verify.cpython-313.pyc").write_bytes(b"\x00compiled")

            active = root / "active"
            install_corpus(bundle, active, release="0.6.6")
            installed = active / "tasks" / "seeds" / "go-csvlimits"
            self.assertTrue((installed / "verify.py").is_file())
            self.assertFalse((installed / "__pycache__").exists())

            install_corpus(_bundle(root / "next", "shipped"), active,
                           release="0.6.7")
            self.assertFalse((installed / "__pycache__").exists(),
                             "nor may an update bring it in")

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

    def test_a_newer_corpus_installed_here_is_not_overwritten(self):
        """`moonshiner seeds update` must outlast the next service start.

        The corpus a project installs deliberately is newer than the one the
        running release carries. Merging the release in would paste its older
        seeds over the corrected ones and relabel the corpus with the older
        version -- the repair undone, silently, on the next restart.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            active = root / "active"
            install_corpus(_bundle(root, "shipped"), active, release="0.6.6")

            seed = active / "tasks" / "seeds" / "go-csvlimits" / "verify.py"
            seed.write_text("repaired")
            (active / "corpus-version.json").write_text(
                '{"version": "2026.07.21.10"}\n')
            (active / ".installed-release").unlink()

            install_corpus(_bundle(root / "same", "shipped"), active,
                           release="0.6.6")
            self.assertEqual("repaired", seed.read_text(),
                             "an older bundled seed must not replace it")
            self.assertIn("2026.07.21.10",
                          (active / "corpus-version.json").read_text(),
                          "nor may the corpus be relabelled with the older version")


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

"""What validation puts a seed into, and what it is willing to read.

Each case here is a defect that made a solvable seed report invalid, so no
patch could ever have rescued it and its whole attempt budget was spent
proving that.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import common  # noqa: E402
import validate_seeds  # noqa: E402

STALE_HEADER_PATCH = """\
diff --git a/value.txt b/value.txt
--- a/value.txt
+++ b/value.txt
@@ -1,4 +1,4 @@
 first
-second
+SECOND
 third
"""


class AHandWrittenPatchIsReadByItsBody(unittest.TestCase):
    """A hunk header's arithmetic is not part of what a seed promises."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "value.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "init", "-q", "."], cwd=self.root, check=True)
        self.patch = self.root / "fix.patch"
        self.patch.write_text(STALE_HEADER_PATCH)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, *flags):
        return subprocess.run([*flags, str(self.patch)], cwd=self.root,
                              capture_output=True, text=True)

    def test_the_counted_header_alone_would_reject_it(self):
        rejected = self.apply("git", "apply")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("corrupt patch", rejected.stderr)

    def test_validation_applies_it_and_can_take_it_back(self):
        self.assertEqual(self.apply(*validate_seeds.PATCH_APPLY).returncode, 0)
        self.assertEqual((self.root / "value.txt").read_text(),
                         "first\nSECOND\nthird\n")
        self.assertEqual(
            self.apply(*validate_seeds.PATCH_APPLY, "-R").returncode, 0)
        self.assertEqual((self.root / "value.txt").read_text(),
                         "first\nsecond\nthird\n")


class TheWorkspaceIsMountedWhereItSaysItIs(unittest.TestCase):
    """`find "$(dirname -- "$0")"` is how a shell verifier names its own tree.

    The mount point used to be `/srv`, which on an ostree host is a symlink to
    `var/srv`. bubblewrap binds through the symlink, so the files arrived but
    the name the seed was handed did not lead to them: `find` walked the
    symlink itself and stopped, and every seed that compiled a found list --
    every `javac @sources.txt` seed among them -- was handed nothing.
    """

    def sandbox_command(self):
        with tempfile.TemporaryDirectory() as name:
            workspace = pathlib.Path(name) / "work"
            workspace.mkdir()
            completed = subprocess.CompletedProcess([], 0, "", "")
            with mock.patch("runtimes.base.run_with_inactivity_timeout",
                            return_value=completed) as run:
                common._sandboxed_command(["true"], workspace, 10)
            return run.call_args.args[0]

    def test_the_mount_point_is_a_directory_and_not_a_link_to_one(self):
        command = self.sandbox_command()
        mount = command[command.index("--chdir") + 1]
        self.assertFalse(pathlib.Path(mount).is_symlink(), mount)
        self.assertEqual(str(pathlib.Path(mount).resolve()), mount)

    def test_the_seed_is_chdired_into_what_was_bound(self):
        command = self.sandbox_command()
        triples = [command[index:index + 3] for index in range(len(command) - 2)]
        mount = command[command.index("--chdir") + 1]
        self.assertTrue(any(triple[0] == "--bind" and triple[2] == mount
                            for triple in triples), command)


class AWarmUpTriesAgainWhenTheBuildWasBroken(unittest.TestCase):
    """A baseline is broken on purpose, and a broken build fetches nothing.

    `java-bakeplan` has no `pom.xml` at all until its reference fix creates
    one, so warming once at baseline left the run that matters with a cold
    cache and no network to fill it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        # The scratch a warm-up records itself in is paired with the workspace
        # by name under the project's own state, so the test needs its own.
        self.workspaces = mock.patch.object(common, "WORKSPACES",
                                            root / "workspaces")
        self.workspaces.start()
        self.addCleanup(self.workspaces.stop)
        self.workspace = root / "workspaces" / "work"
        self.workspace.mkdir(parents=True)
        (self.workspace / "go.mod").write_text("module example.test\n")

    def tearDown(self):
        self.tmp.cleanup()

    def warm(self, returncode):
        completed = subprocess.CompletedProcess([], returncode, "", "")
        with mock.patch.object(common, "_sandboxed_command",
                               return_value=completed) as run:
            note = common.warm_dependency_cache(self.workspace)
        return note, run.call_count

    def test_a_warm_up_that_worked_is_not_repeated(self):
        self.assertEqual(self.warm(0), ("go: ok", 1))
        self.assertEqual(self.warm(0), ("(already warmed)", 0))

    def test_a_warm_up_that_failed_is_tried_once_more_and_then_left(self):
        self.assertEqual(self.warm(1), ("go: failed", 1))
        self.assertEqual(self.warm(1), ("go: failed", 1))
        self.assertEqual(self.warm(1), ("(already warmed)", 0))

    def test_a_project_that_starts_declaring_something_is_warmed_for_it(self):
        self.assertEqual(self.warm(0), ("go: ok", 1))
        (self.workspace / "go.mod").write_text("module example.test\n\ngo 1.22\n")
        self.assertEqual(self.warm(0), ("go: ok", 1))


if __name__ == "__main__":
    unittest.main()

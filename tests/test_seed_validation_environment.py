"""What validation puts a seed into, and what it is willing to read.

Each case here is a defect that made a solvable seed report invalid, so no
patch could ever have rescued it and its whole attempt budget was spent
proving that.
"""
import pathlib
import signal
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


class CondaSurvivesTheHiddenHome(unittest.TestCase):
    """The sandbox hides the home conda is installed in, PATH notwithstanding.

    `effective_path()` advertises conda's `bin` because it is genuinely on the
    host, but the mount that hides the user's home hides conda with it. Four
    seeds whose setup is `conda env create -p ./env` failed with
    `bwrap: execvp conda: No such file or directory`.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.conda = self.home / "miniconda3"
        (self.conda / "conda-meta").mkdir(parents=True)
        (self.conda / "pkgs").mkdir()
        terms = self.home / ".conda" / "tos" / "channel"
        terms.mkdir(parents=True)
        (terms / "accepted.json").write_text('{"tos_accepted": true}')
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir()
        # The host reaches its home through one: `/home` is a symlink to
        # `/var/home` on an ostree system, so PATH and the resolved
        # installation disagree on spelling for the same directory.
        self.linked_home = self.root / "linked-home"
        self.linked_home.symlink_to(self.home)
        patches = [
            mock.patch.object(common.Path, "home", classmethod(
                lambda cls, home=self.home: home)),
            mock.patch.object(common, "WORKSPACES", self.workspaces),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def sandbox_command(self, installed=True):
        workspace = self.workspaces / "work"
        workspace.mkdir(exist_ok=True)
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(common, "conda_installation",
                               return_value=self.conda if installed else None):
            with mock.patch("runtimes.base.run_with_inactivity_timeout",
                            return_value=completed) as run:
                common._sandboxed_command(["true"], workspace, 10)
        return run.call_args.args[0]

    def test_conda_is_bound_back_after_the_mount_that_hides_it(self):
        command = self.sandbox_command()
        hides_home = next(index for index in range(len(command) - 2)
                          if command[index] == "--bind"
                          and command[index + 2] == str(self.home))
        exposes_conda = next(index for index in range(len(command) - 2)
                             if command[index] == "--ro-bind"
                             and command[index + 1] == str(self.conda)
                             and command[index + 2] == str(self.conda))
        self.assertGreater(exposes_conda, hides_home, command)

    def test_the_acceptance_the_user_already_made_is_carried_in(self):
        self.sandbox_command()
        scratch = common.verify_scratch(self.workspaces / "work")
        carried = scratch / "tmp" / ".sandbox-home" / ".conda" / "tos"
        self.assertTrue((carried / "channel" / "accepted.json").is_file())
        self.assertFalse((scratch / "tmp" / ".sandbox-home" / ".conda"
                          / "aau_token").exists())

    def test_extraction_has_somewhere_to_write_and_still_reads_the_cache(self):
        command = self.sandbox_command()
        value = command[command.index("CONDA_PKGS_DIRS") + 1]
        writable, _, cached = value.partition(",")
        self.assertTrue(writable.startswith("/tmp/.sandbox-home"), value)
        self.assertEqual(cached, str(self.conda / "pkgs"))

    def test_conda_never_gets_to_decide_what_python3_means(self):
        # Named through the symlink, the way the host's own PATH names it.
        # Compared as text this does not look like the installation at all,
        # so the reordering matched nothing and Anaconda's python -- which
        # has no `os.pidfd_open` -- went on answering `python3` and broke a
        # seed that has nothing to do with conda.
        advertised = f"{self.linked_home}/miniconda3/bin"
        with mock.patch("toolchains.effective_path",
                        return_value=f"{advertised}:/usr/bin:/bin"):
            command = self.sandbox_command()
        entries = command[command.index("PATH") + 1].split(":")
        self.assertLess(entries.index("/usr/bin"),
                        entries.index(advertised), entries)
        self.assertIn(advertised, entries)

    def test_a_host_without_conda_is_left_exactly_as_it_was(self):
        command = self.sandbox_command(installed=False)
        self.assertNotIn("CONDA_PKGS_DIRS", command)
        self.assertNotIn(str(self.conda), command)


class TheTelemetryInvitationIsNotSeedOutput(unittest.TestCase):
    """PowerCLI's CEIP banner landed in the middle of `vcfarch-0073`'s JSON."""

    def test_the_answer_is_already_in_the_home_the_sandbox_gets(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        workspaces = pathlib.Path(root.name).resolve() / "workspaces"
        workspace = workspaces / "work"
        workspace.mkdir(parents=True)
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(common, "WORKSPACES", workspaces), \
                mock.patch("runtimes.base.run_with_inactivity_timeout",
                           return_value=completed):
            common._sandboxed_command(["true"], workspace, 10)
            scratch = common.verify_scratch(workspace)
        settings = (scratch / "tmp" / ".sandbox-home" / ".local" / "share"
                    / "VMware" / "PowerCLI" / "PowerCLI_Settings.xml")
        self.assertIn('Name="ParticipateInCEIP" Value="False"',
                      settings.read_text())


TRAP_HUP = """\
trap 'exit 42' HUP
kill -HUP $$
sleep 1
exit 7
"""


class NohupMustNotDecideWhetherASeedPasses(unittest.TestCase):
    """`bash-background-cleanup` reported invalid because of how I ran it.

    `SIG_IGN` is inherited through every fork and every exec below it, and a
    shell will not install a trap for a signal that arrived already ignored.
    Started under `nohup`, the pipeline handed the sandbox an ignored SIGHUP,
    and the one seed whose subject is signal handling could not trap it.
    """

    def test_an_ignored_signal_does_not_reach_the_sandbox(self):
        previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGHUP, previous)
        inherited = subprocess.run(["bash", "-c", TRAP_HUP])
        self.assertEqual(inherited.returncode, 7,
                         "an ignored SIGHUP should not be trappable")
        reset = subprocess.run(
            [*common.default_signal_dispositions(), "bash", "-c", TRAP_HUP])
        self.assertEqual(reset.returncode, 42, "the trap should have run")

    def test_the_reset_happens_before_the_sandbox_is_entered(self):
        prefix = common.default_signal_dispositions()
        if not prefix:
            self.skipTest("GNU coreutils env is not installed")
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        workspaces = pathlib.Path(root.name).resolve() / "workspaces"
        workspace = workspaces / "work"
        workspace.mkdir(parents=True)
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(common, "WORKSPACES", workspaces), \
                mock.patch("runtimes.base.run_with_inactivity_timeout",
                           return_value=completed) as run:
            common._sandboxed_command(["true"], workspace, 10)
        command = run.call_args.args[0]
        self.assertEqual(command[:len(prefix)], prefix)
        self.assertEqual(command[len(prefix)], "bwrap")


TRAILING_BLANK_PATCH = """\
--- a/value.txt
+++ b/value.txt
@@ -1,3 +1,3 @@
 first
-second
+SECOND
 third

"""


class WhatAnEditorAddsAtTheEndIsNotPartOfThePatch(unittest.TestCase):
    """One byte of trailing whitespace kept `vcf91-0220` from ever applying."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "value.txt").write_text("first\nsecond\nthird\n")
        subprocess.run(["git", "init", "-q", "."], cwd=self.root, check=True)
        self.patch = self.root / "fix.patch"
        self.patch.write_text(TRAILING_BLANK_PATCH)
        self.addCleanup(self.tmp.cleanup)

    def test_git_alone_reads_the_blank_line_as_a_line_the_file_must_have(self):
        plain = subprocess.run(["git", "apply", "--recount", str(self.patch)],
                               cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(plain.returncode, 0)
        self.assertIn("does not apply", plain.stderr)

    def test_validation_applies_it_and_reverses_it(self):
        applied = validate_seeds.apply_patch(self.patch, self.root)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((self.root / "value.txt").read_text(),
                         "first\nSECOND\nthird\n")
        reversed_ = validate_seeds.apply_patch(self.patch, self.root, "-R")
        self.assertEqual(reversed_.returncode, 0, reversed_.stderr)
        self.assertEqual((self.root / "value.txt").read_text(),
                         "first\nsecond\nthird\n")

    def test_a_blank_line_the_patch_really_adds_is_still_added(self):
        self.patch.write_text("--- a/value.txt\n+++ b/value.txt\n"
                              "@@ -1,3 +1,4 @@\n first\n second\n third\n+\n")
        applied = validate_seeds.apply_patch(self.patch, self.root)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual((self.root / "value.txt").read_text(),
                         "first\nsecond\nthird\n\n")

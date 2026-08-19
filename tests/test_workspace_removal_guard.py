"""Nothing but a workspace may ever be deleted.

On 2026-07-27 an unguarded ``shutil.rmtree`` in ``preflight_seed_environment``
deleted an entire project directory: its test doubles ``materialize`` to return
the repository root, which was harmless until that function started removing
what materialize returned. ``ignore_errors=True`` made it silent and the test
still passed. These cases exist so that cannot recur.
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402


class RemovalGuard(unittest.TestCase):
    def _workspaces(self, root: pathlib.Path):
        return mock.patch.object(common, "WORKSPACES", root)

    def test_removes_a_real_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp); ws = root / "seed-a"
            (ws / "files").mkdir(parents=True)
            (ws / "files" / "x").write_text("x")
            with self._workspaces(root):
                common.remove_workspace(ws)
            self.assertFalse(ws.exists())

    def test_refuses_the_repository_root(self):
        """The exact path that destroyed the project."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "workspaces"; root.mkdir()
            repo = pathlib.Path(tmp) / "repo"; (repo / ".git").mkdir(parents=True)
            with self._workspaces(root), self.assertRaises(ValueError):
                common.remove_workspace(repo)
            self.assertTrue((repo / ".git").exists())

    def test_refuses_the_workspaces_directory_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "workspaces"; root.mkdir()
            with self._workspaces(root), self.assertRaises(ValueError):
                common.remove_workspace(root)
            self.assertTrue(root.exists())

    def test_refuses_a_parent_of_the_workspaces_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "state" / "workspaces"
            root.mkdir(parents=True)
            with self._workspaces(root), self.assertRaises(ValueError):
                common.remove_workspace(root.parent)
            self.assertTrue(root.exists())

    def test_refuses_a_symlink_escaping_the_workspaces_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "workspaces"; root.mkdir()
            outside = pathlib.Path(tmp) / "precious"; outside.mkdir()
            (outside / "keep").write_text("keep")
            (root / "escape").symlink_to(outside, target_is_directory=True)
            with self._workspaces(root), self.assertRaises(ValueError):
                common.remove_workspace(root / "escape")
            self.assertTrue((outside / "keep").exists())

    def test_refuses_a_relative_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "workspaces"; root.mkdir()
            outside = pathlib.Path(tmp) / "precious"; outside.mkdir()
            with self._workspaces(root), self.assertRaises(ValueError):
                common.remove_workspace(root / ".." / "precious")
            self.assertTrue(outside.exists())

    def test_a_missing_workspace_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with self._workspaces(root):
                common.remove_workspace(root / "never-existed")

    def test_read_only_tree_is_still_removed(self):
        """Go writes its module cache read-only; removal must not abandon it."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp); ws = root / "seed-go"
            cache = ws / "go" / "pkg" / "mod" / "dep@v1"
            cache.mkdir(parents=True)
            (cache / "x.go").write_text("package x\n")
            (cache / "x.go").chmod(0o444)
            cache.chmod(0o555); cache.parent.chmod(0o555)
            with self._workspaces(root):
                common.remove_workspace(ws)
            self.assertFalse(ws.exists())

    def test_a_directory_the_agent_made_unreadable_is_still_removed(self):
        """OpenCode leaves `tmp/opencode/hide` at mode 111.

        ``rmtree`` cannot open such a directory to walk it, and the error
        handler is then called with ``os.open`` -- which needs flags it was
        never given, so the removal died on a ``TypeError`` that hid the
        permission error underneath. Two seeds sat blocked behind that.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp); ws = root / "seed-hidden"
            hidden = ws / "tmp" / "opencode" / "hide"
            hidden.mkdir(parents=True)
            (hidden / "secret").write_text("x")
            hidden.chmod(0o111)
            (ws / ".sandbox-home" / "psstore").mkdir(parents=True)
            (ws / ".sandbox-home" / "psstore").chmod(0o111)
            with self._workspaces(root):
                common.remove_workspace(ws)
            self.assertFalse(ws.exists())

    def test_removal_never_chmods_through_an_escaping_symlink(self):
        """Clearing permission bits must not reach outside the workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "workspaces"; root.mkdir()
            outside = pathlib.Path(tmp) / "precious"; outside.mkdir()
            (outside / "keep").write_text("keep")
            (outside / "keep").chmod(0o400)
            ws = root / "seed-link"; ws.mkdir()
            (ws / "escape").symlink_to(outside, target_is_directory=True)
            before = outside.stat().st_mode & 0o777
            with self._workspaces(root):
                common.remove_workspace(ws)
            self.assertFalse(ws.exists())
            self.assertTrue((outside / "keep").exists())
            self.assertEqual((outside / "keep").stat().st_mode & 0o777, 0o400)
            self.assertEqual(outside.stat().st_mode & 0o777, before)


class PreflightNeverDeletesOutsideWorkspaces(unittest.TestCase):
    def test_a_materialize_double_returning_the_repo_root_deletes_nothing(self):
        """Reproduces the original failure end to end."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "workspaces"; root.mkdir()
            repo = pathlib.Path(tmp) / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / "src").mkdir()
            (repo / "src" / "common.py").write_text("# source")
            with mock.patch.object(common, "WORKSPACES", root), \
                 mock.patch.object(common, "materialize", return_value=repo), \
                 mock.patch.object(common, "run_verify",
                                   return_value=(False, "expected baseline failure")):
                ready, _ = common.preflight_seed_environment({"id": "s"})
            self.assertTrue(ready)
            self.assertTrue((repo / ".git").exists(), "the repository must survive")
            self.assertTrue((repo / "src" / "common.py").exists())


if __name__ == "__main__":
    unittest.main()

"""Codex command construction: reviews must work outside a git checkout."""
import json
import inspect
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.availability import ModelUnavailable  # noqa: E402
from runtimes.base import workspace_only_command  # noqa: E402


def _runtime(runtime_config=None):
    config = {"runtimes": {"codex": runtime_config or {}}}
    return CodexRuntime(config, {"model": "test-model", "reasoning": "xhigh"})


class BaseCommand(unittest.TestCase):
    def test_skips_the_git_repo_check_by_default(self):
        """Archived attempt directories are file copies, never checkouts.

        Codex refuses an untrusted non-repository working directory, which is
        what the synthetic-corrections eligibility review hands it.
        """
        cmd = _runtime()._base_cmd(sandbox="read-only")
        self.assertIn("--skip-git-repo-check", cmd)

    def test_can_be_disabled_by_configuration(self):
        cmd = _runtime({"skip_git_repo_check": False})._base_cmd(sandbox="read-only")
        self.assertNotIn("--skip-git-repo-check", cmd)

    def test_model_flag_keeps_its_value_adjacent(self):
        cmd = _runtime()._base_cmd(sandbox="read-only")
        self.assertEqual(cmd[cmd.index("--model") + 1], "test-model")

    def test_outer_boundary_is_the_only_codex_filesystem_sandbox(self):
        for operation in (CodexRuntime.run_trace, CodexRuntime.run_review):
            with self.subTest(operation=operation.__name__):
                source = inspect.getsource(operation)
                self.assertIn('_base_cmd(sandbox="danger-full-access"', source)

    def test_outer_boundary_enforces_read_only_reviews(self):
        with tempfile.TemporaryDirectory() as name:
            workspace = pathlib.Path(name) / "workspace"
            workspace.mkdir()
            command = workspace_only_command(
                ["true"], workspace, workspace_writable=False)
        workspace_mount = ["--ro-bind", str(workspace.resolve()),
                           str(workspace.resolve())]
        self.assertIn(workspace_mount,
                      [command[index:index + 3]
                       for index in range(len(command) - 2)])


class ReviewAvailability(unittest.TestCase):
    def test_usage_limit_reported_in_event_stream_stops_the_review(self):
        message = ("You've hit your usage limit. Visit https://example.invalid/usage "
                   "to purchase more credits or try again later.")
        stdout = (json.dumps({"type": "error", "message": message}) + "\n"
                  + json.dumps({"type": "turn.failed",
                                "error": {"message": message}}) + "\n")
        with tempfile.TemporaryDirectory() as name:
            workspace = pathlib.Path(name) / "workspace"; workspace.mkdir()
            out_dir = pathlib.Path(name) / "out"; out_dir.mkdir()
            completed = SimpleNamespace(returncode=1, stdout=stdout, stderr="")
            with mock.patch.object(CodexRuntime, "require_persistent_workspace",
                                   return_value=workspace), \
                 mock.patch("runtimes.codex.run_with_inactivity_timeout",
                            return_value=completed):
                with self.assertRaisesRegex(ModelUnavailable, "usage limit"):
                    _runtime().run_review("review", workspace, out_dir=out_dir)


if __name__ == "__main__":
    unittest.main()

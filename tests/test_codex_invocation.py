"""Codex command construction: reviews must work outside a git checkout."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runtimes.codex import CodexRuntime  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()

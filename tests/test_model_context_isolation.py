"""No model process may inherit Moonshiner's repository instructions."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import configuration  # noqa: E402
import seed_pipeline  # noqa: E402
import security_runtime  # noqa: E402
from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402
from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.pi import PiRuntime  # noqa: E402


class WorkspaceLocation(unittest.TestCase):
    def test_default_model_workspaces_are_outside_the_project(self):
        project = configuration.PROJECT_ROOT.resolve()
        workspace_root = common.WORKSPACES.resolve()
        self.assertNotEqual(workspace_root, project)
        self.assertNotIn(project, workspace_root.parents)
        self.assertEqual(seed_pipeline.CANDIDATES.parent, common.WORKSPACES)

    def test_project_local_data_home_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = pathlib.Path(directory) / "repo"
            project.mkdir()
            with self.assertRaisesRegex(RuntimeError, "outside the project"):
                common.model_workspace_root(
                    project_root=project, data_home=project / ".data")

    def test_project_key_keeps_external_workspaces_project_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            data_home = root / "data"
            first = common.model_workspace_root(
                project_root=root / "first", data_home=data_home)
            second = common.model_workspace_root(
                project_root=root / "second", data_home=data_home)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent.parent.parent, data_home / "moonshiner")

    def test_any_agents_file_in_workspace_ancestry_blocks_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "AGENTS.md").write_text("instructions for the coding agent\n")
            workspace = root / "workspaces" / "candidate"
            workspace.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "contains AGENTS.md"):
                CodexRuntime.require_persistent_workspace(workspace)

    def test_repository_descendant_blocks_launch_even_without_scanning_rules(self):
        workspace = configuration.PROJECT_ROOT / ".moonshiner" / "workspaces" / "x"
        with self.assertRaisesRegex(RuntimeError, "outside the project repository"):
            CodexRuntime.require_persistent_workspace(workspace)

    def test_archived_review_inputs_are_staged_externally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "archive"
            source.mkdir()
            (source / "trace.jsonl").write_text("{}\n")
            workspaces = root / "external" / "workspaces"
            with mock.patch.object(common, "WORKSPACES", workspaces):
                staged = common.stage_workspace(source, "eligibility-seed")
                self.assertEqual((staged / "trace.jsonl").read_text(), "{}\n")
                self.assertIn(workspaces.resolve(), staged.resolve().parents)
                common.remove_workspace(staged)
            self.assertFalse(staged.exists())


class EveryModelEntryPointUsesTheBoundary(unittest.TestCase):
    def _runtime(self, runtime_class):
        name = runtime_class.name
        config = {"runtimes": {name: {}}, "trace": {}}
        return runtime_class(config, {"model": "test-model", "reasoning": "low"})

    def _assert_blocked(self, runtime, call):
        boundary = RuntimeError("context boundary reached")
        with mock.patch.object(runtime, "require_persistent_workspace",
                               side_effect=boundary) as require:
            with self.assertRaisesRegex(RuntimeError, "context boundary reached"):
                call(runtime)
        require.assert_called_once()

    def test_every_teacher_adapter_checks_before_launch(self):
        calls = (
            (CodexRuntime, lambda runtime: runtime.run_trace(
                {"id": "seed"}, pathlib.Path("workspace"),
                out_dir=pathlib.Path("out"), system_prompt="system", prompt="prompt")),
            (PiRuntime, lambda runtime: runtime.run_trace(
                {"id": "seed"}, pathlib.Path("workspace"),
                out_dir=pathlib.Path("out"), system_prompt="system", prompt="prompt")),
            (ClaudeCodeRuntime, lambda runtime: runtime.run_trace(
                {"id": "seed"}, pathlib.Path("workspace"),
                out_dir=pathlib.Path("out"), system_prompt="system", prompt="prompt")),
        )
        for runtime_class, call in calls:
            with self.subTest(runtime=runtime_class.name):
                self._assert_blocked(self._runtime(runtime_class), call)

    def test_every_judge_adapter_checks_before_launch(self):
        for runtime_class in (CodexRuntime, PiRuntime, ClaudeCodeRuntime):
            with self.subTest(runtime=runtime_class.name):
                self._assert_blocked(
                    self._runtime(runtime_class),
                    lambda runtime: runtime.run_review(
                        "review", pathlib.Path("workspace"),
                        out_dir=pathlib.Path("out"), read_only=False))

    def test_contained_security_runtime_hides_repo_and_uses_neutral_paths(self):
        project = configuration.PROJECT_ROOT.resolve()
        host_workspace = project / ".moonshiner" / "security-work" / "case"
        host_codex_home = project / ".moonshiner" / "security-runtime" / "home"
        inner = ["codex", "exec", "-C", str(security_runtime.SANDBOX_WORKSPACE)]
        with mock.patch.object(security_runtime.shutil, "which",
                               return_value="/usr/bin/bwrap"):
            command = security_runtime._outer_sandbox(
                inner, host_workspace, host_codex_home)
        hidden = [command[index + 1] for index, value in enumerate(command)
                  if value == "--tmpfs"]
        self.assertTrue(any(pathlib.Path(path) == project
                            or pathlib.Path(path) in project.parents
                            for path in hidden))
        self.assertIn(str(security_runtime.SANDBOX_WORKSPACE), command)
        self.assertIn(str(security_runtime.SANDBOX_CODEX_HOME), command)
        self.assertEqual(command[command.index("--chdir") + 1],
                         str(security_runtime.SANDBOX_WORKSPACE))
        inner_command = command[command.index("--") + 1:]
        self.assertEqual(inner_command[inner_command.index("-C") + 1],
                         str(security_runtime.SANDBOX_WORKSPACE))
        self.assertNotIn(str(host_workspace), inner_command)
        self.assertNotIn(str(host_codex_home), inner_command)


if __name__ == "__main__":
    unittest.main()

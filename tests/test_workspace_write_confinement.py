"""Every runtime-owned write is physically confined to its job workspace."""
from __future__ import annotations

import ast
import inspect
import pathlib
import subprocess
import sys
import uuid
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import configuration  # noqa: E402
import security_runtime  # noqa: E402
from runtimes.base import Runtime  # noqa: E402
from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402
from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.pi import PiRuntime  # noqa: E402


class WorkspaceOwnedEnvironment(unittest.TestCase):
    def test_every_mutable_runtime_path_is_inside_the_workspace(self):
        workspace = common.WORKSPACES / f"confinement-env-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        environment = Runtime.teacher_environment(workspace)
        for name in (
                "HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                "DOTNET_CLI_HOME", "NUGET_PACKAGES", "GOCACHE", "GOMODCACHE",
                "GOPATH", "TMPDIR", "TMP", "TEMP", "CODEX_HOME",
                "CLAUDE_CONFIG_DIR"):
            with self.subTest(name=name):
                self.assertTrue(pathlib.Path(environment[name]).resolve().is_relative_to(
                    workspace.resolve()))


class KernelEnforcedWriteBoundary(unittest.TestCase):
    def test_host_tmp_and_sibling_writes_land_nowhere_outside_workspace(self):
        workspace = common.WORKSPACES / f"confinement-run-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        sibling = workspace.parent / f"escape-{uuid.uuid4().hex}"
        host_tmp = pathlib.Path("/tmp") / f"escape-{uuid.uuid4().hex}"
        host_var_tmp = pathlib.Path("/var/tmp") / f"escape-{uuid.uuid4().hex}"
        host_shm = pathlib.Path("/dev/shm") / f"escape-{uuid.uuid4().hex}"
        script = (
            "import os,pathlib\n"
            "pathlib.Path(os.environ['TMPDIR'],'api-temp').write_text('ok')\n"
            f"pathlib.Path('/tmp/{host_tmp.name}').write_text('tmp')\n"
            f"pathlib.Path('/var/tmp/{host_var_tmp.name}').write_text('var-tmp')\n"
            f"pathlib.Path('/dev/shm/{host_shm.name}').write_text('shm')\n"
            f"\ntry: pathlib.Path({str(sibling)!r}).write_text('escape')\n"
            "except OSError: pass\n"
            "else: raise SystemExit('sibling write escaped workspace')\n"
            f"\ntry: pathlib.Path({str(configuration.PROJECT_ROOT)!r},'AGENTS.md').read_text()\n"
            "except OSError: pass\n"
            "else: raise SystemExit('repository context escaped into model')\n"
            f"\ntry: pathlib.Path({str(pathlib.Path.home() / '.codex' / 'auth.json')!r}).read_text()\n"
            "except OSError: pass\n"
            "else: raise SystemExit('host credentials escaped into model')\n")
        from runtimes.base import workspace_only_command
        environment = Runtime.teacher_environment(workspace)
        result = subprocess.run(
            workspace_only_command([sys.executable, "-c", script], workspace),
            cwd=workspace, env=environment, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        scratch = workspace / ".sandbox-home" / "tmp"
        self.assertEqual("ok", (scratch / "api-temp").read_text())
        self.assertEqual("tmp", (scratch / host_tmp.name).read_text())
        self.assertEqual("var-tmp", (scratch / host_var_tmp.name).read_text())
        self.assertEqual("shm", (workspace / ".sandbox-home" / "shm" /
                                 host_shm.name).read_text())
        self.assertFalse(host_tmp.exists())
        self.assertFalse(host_var_tmp.exists())
        self.assertFalse(host_shm.exists())
        self.assertFalse(sibling.exists())

    def test_every_model_runtime_launches_through_the_boundary(self):
        for operation in (CodexRuntime.run_trace, ClaudeCodeRuntime.run_trace):
            with self.subTest(operation=operation.__qualname__):
                self.assertIn("prepare_trace_command(", inspect.getsource(operation))
        for operation in (CodexRuntime.run_review, ClaudeCodeRuntime.run_review):
            with self.subTest(operation=operation.__qualname__):
                self.assertIn("workspace_only_command(", inspect.getsource(operation))
        pi_source = inspect.getsource(PiRuntime._run)
        self.assertIn("prepare_trace_command(", pi_source)
        self.assertIn("workspace_only_command(", pi_source)

    def test_seed_commands_use_only_workspace_backed_writable_storage(self):
        workspace = common.WORKSPACES / f"confinement-verify-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        script = (
            "import os,pathlib\n"
            "pathlib.Path(os.environ['HOME'],'home-write').write_text('home')\n"
            "pathlib.Path('/tmp/tmp-write').write_text('tmp')\n"
            "pathlib.Path('/var/tmp/var-tmp-write').write_text('var-tmp')\n"
            "pathlib.Path('/dev/shm/shm-write').write_text('shm')\n")
        result = common._sandboxed_command(
            ["/usr/bin/python3", "-c", script], workspace, 30)
        self.assertEqual(0, result.returncode, result.stderr)
        home = workspace / ".sandbox-home"
        self.assertEqual("home", (home / "home-write").read_text())
        self.assertEqual("tmp", (home / "tmp" / "tmp-write").read_text())
        self.assertEqual("var-tmp", (home / "tmp" / "var-tmp-write").read_text())
        self.assertEqual("shm", (home / "shm" / "shm-write").read_text())

    def test_security_runtime_uses_the_same_physical_write_boundary(self):
        workspace = common.WORKSPACES / f"confinement-security-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        codex_home = workspace / ".sandbox-home" / "security-codex"
        codex_home.mkdir(parents=True)
        script = (
            "import pathlib\n"
            f"pathlib.Path({str(security_runtime.SANDBOX_WORKSPACE)!r},'work').write_text('work')\n"
            f"pathlib.Path({str(security_runtime.SANDBOX_CODEX_HOME)!r},'state').write_text('state')\n"
            "pathlib.Path('/tmp/tmp-write').write_text('tmp')\n"
            "pathlib.Path('/var/tmp/var-tmp-write').write_text('var-tmp')\n"
            "pathlib.Path('/dev/shm/shm-write').write_text('shm')\n")
        result = subprocess.run(
            security_runtime._outer_sandbox(
                ["/usr/bin/python3", "-c", script], workspace, codex_home),
            cwd=workspace, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        mount_root = workspace / ".sandbox-home" / "security-runtime" / "mounts"
        self.assertEqual("work", (workspace / "work").read_text())
        self.assertEqual("state", (codex_home / "state").read_text())
        self.assertEqual("tmp", (mount_root / "tmp" / "tmp-write").read_text())
        self.assertEqual("var-tmp", (mount_root / "tmp" / "var-tmp-write").read_text())
        self.assertEqual("shm", (mount_root / "shm" / "shm-write").read_text())

    def test_no_runtime_mounts_an_anonymous_or_host_temporary_directory(self):
        for path in (ROOT / "src").glob("**/*.py"):
            source = path.read_text()
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn('"--tmpfs", "/tmp"', source)
                self.assertNotIn('"--tmpfs", "/var/tmp"', source)

    def test_product_temporary_directories_always_name_a_workspace_owned_root(self):
        for path in (ROOT / "src").glob("**/*.py"):
            tree = ast.parse(path.read_text())
            for call in (node for node in ast.walk(tree)
                         if isinstance(node, ast.Call)):
                function = call.func
                if not (isinstance(function, ast.Attribute)
                        and isinstance(function.value, ast.Name)
                        and function.value.id == "tempfile"
                        and function.attr in {"TemporaryDirectory", "mkdtemp"}):
                    continue
                with self.subTest(path=path.relative_to(ROOT), line=call.lineno):
                    self.assertIn("dir", {keyword.arg for keyword in call.keywords})

    def test_shell_entry_points_do_not_use_the_host_temporary_default(self):
        for path in (ROOT / "install.sh", ROOT / "scripts" / "check.sh",
                     ROOT / ".github" / "workflows" / "release.yml"):
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text()
                self.assertNotIn("mktemp -d)", source)
                self.assertNotIn("mktemp -d -t", source)
                self.assertNotIn(": /tmp/", source)
                self.assertNotIn(" /tmp/", source)


if __name__ == "__main__":
    unittest.main()

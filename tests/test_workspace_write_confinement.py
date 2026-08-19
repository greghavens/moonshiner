"""Every runtime-owned write is physically confined to its job workspace."""
from __future__ import annotations

import ast
import inspect
import pathlib
import shutil
import subprocess
import sys
import tempfile
import uuid
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import configuration  # noqa: E402
import security_runtime  # noqa: E402
import toolchains  # noqa: E402
from runtimes.base import Runtime  # noqa: E402
from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402
from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.opencode import OpenCodeRuntime  # noqa: E402
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


class PeerWorkspacesAreInvisible(unittest.TestCase):
    """Every job workspace shares one root, and the sandbox bounded only
    writes. A seed-authoring agent walked that root, read two neighbours'
    task.json, copied a third's verify.py into its scratch and ran it -- so a
    candidate was being shaped by other in-flight, unjudged seeds.
    """

    def sandbox(self, workspace: pathlib.Path, script: str):
        from runtimes.base import workspace_only_command
        return subprocess.run(
            workspace_only_command([sys.executable, "-c", script], workspace),
            cwd=workspace, env=Runtime.teacher_environment(workspace),
            capture_output=True, text=True)

    def pair(self) -> tuple[pathlib.Path, pathlib.Path]:
        workspace = common.WORKSPACES / f"confinement-own-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        peer = common.WORKSPACES / f"confinement-peer-{uuid.uuid4().hex}"
        (peer / "seed").mkdir(parents=True)
        self.addCleanup(common.remove_workspace, peer)
        (peer / "seed" / "task.json").write_text('{"id":"neighbour"}')
        return workspace, peer

    def test_a_neighbouring_workspace_cannot_be_read(self):
        workspace, peer = self.pair()
        result = self.sandbox(workspace, (
            "import pathlib\n"
            f"try: pathlib.Path({str(peer / 'seed' / 'task.json')!r}).read_text()\n"
            "except OSError: pass\n"
            "else: raise SystemExit('a peer workspace leaked into the agent')\n"))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_the_shared_root_lists_nothing_but_this_workspace(self):
        # Reading a named neighbour is the symptom; enumerating the root is how
        # the agent found them in the first place.
        workspace, _ = self.pair()
        result = self.sandbox(workspace, (
            "import pathlib\n"
            f"visible = sorted(entry.name for entry in "
            f"pathlib.Path({str(common.WORKSPACES)!r}).iterdir())\n"
            f"assert visible == [{workspace.name!r}], visible\n"))
        self.assertEqual(0, result.returncode, result.stderr)

    def test_the_agent_still_owns_its_own_workspace(self):
        workspace, _ = self.pair()
        (workspace / "brief.txt").write_text("mine")
        result = self.sandbox(workspace, (
            "import pathlib\n"
            f"root = pathlib.Path({str(workspace)!r})\n"
            "assert (root / 'brief.txt').read_text() == 'mine'\n"
            "(root / 'authored.txt').write_text('work')\n"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("work", (workspace / "authored.txt").read_text())

    def test_a_workspace_outside_the_shared_root_is_left_alone(self):
        # Tests and one-off runs build workspaces elsewhere; masking a root
        # they do not live under would hide the tree they are running in.
        from runtimes.base import _peer_workspace_mask
        outside = pathlib.Path(tempfile.mkdtemp(dir=ROOT))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.assertEqual([], _peer_workspace_mask(outside, outside / "masks"))


class ProvisionedModulesSurviveTheProjectMask(unittest.TestCase):
    """A seed promises modules as already installed; the agent must see them.

    They are provisioned under the project directory, which this sandbox
    blanks so no agent can read the corpus, the ledger or another job's
    traces. The agent was therefore handed a ``PSModulePath`` naming a
    directory its own sandbox had just emptied. 111 seeds declare a module
    this way, and most only ask for a script the verifier runs later, so the
    absence stayed hidden behind them. It surfaces on the ones whose work
    needs the module loaded: one asked the operator how to proceed, which a
    headless trace cannot answer, and one spent itself installing from the
    gallery -- which those same seeds forbid.
    """

    def sandbox(self, workspace: pathlib.Path, script: str):
        from runtimes.base import workspace_only_command
        return subprocess.run(
            workspace_only_command([sys.executable, "-c", script], workspace),
            cwd=workspace, env=Runtime.teacher_environment(workspace),
            capture_output=True, text=True)

    def project(self) -> tuple[pathlib.Path, pathlib.Path]:
        project = pathlib.Path(tempfile.mkdtemp(dir=ROOT))
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        module_root = (project / ".moonshiner" / "toolchains" / "powershell"
                       / "Modules")
        version = module_root / "Promised.Module" / "1.2.3"
        version.mkdir(parents=True)
        (version / "Promised.Module.psd1").write_text("provisioned")
        (project / ".moonshiner" / "traces").mkdir(parents=True)
        (project / ".moonshiner" / "traces" / "candidate.json").write_text("{}")
        return project, module_root

    def test_the_modules_stay_readable_and_the_rest_of_the_project_does_not(self):
        project, module_root = self.project()
        workspace = common.WORKSPACES / f"confinement-modules-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        promised = module_root / "Promised.Module" / "1.2.3" / "Promised.Module.psd1"
        script = (
            "import pathlib\n"
            f"promised = pathlib.Path({str(promised)!r})\n"
            "assert promised.is_file(), 'a promised module was masked away'\n"
            "assert promised.read_text() == 'provisioned'\n"
            f"hidden = pathlib.Path({str(project / '.moonshiner' / 'traces')!r})\n"
            "assert not hidden.exists(), 'project state leaked into the agent'\n"
            f"root = pathlib.Path({str(project)!r})\n"
            "assert [e.name for e in root.iterdir()] == ['.moonshiner'], "
            "[e.name for e in root.iterdir()]\n")
        with mock.patch.object(configuration, "PROJECT_ROOT", project), \
             mock.patch.object(toolchains, "powershell_module_root",
                               lambda: module_root):
            result = self.sandbox(workspace, script)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_a_module_root_no_mask_covers_is_left_unmounted(self):
        # The restoring bind exists only to undo a mask. Emitting it anyway
        # would mount a host directory into every sandbox for no reason.
        from runtimes.base import workspace_only_command
        project, _ = self.project()
        elsewhere = pathlib.Path(tempfile.mkdtemp(dir=ROOT)) / "Modules"
        elsewhere.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, elsewhere.parent, ignore_errors=True)
        workspace = common.WORKSPACES / f"confinement-unmasked-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        with mock.patch.object(configuration, "PROJECT_ROOT", project), \
             mock.patch.object(toolchains, "powershell_module_root",
                               lambda: elsewhere):
            argv = workspace_only_command(["true"], workspace)
        self.assertNotIn(str(elsewhere), argv)


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
        for operation in (CodexRuntime.run_trace, ClaudeCodeRuntime.run_trace,
                          OpenCodeRuntime.run_trace):
            with self.subTest(operation=operation.__qualname__):
                self.assertIn("prepare_trace_command(", inspect.getsource(operation))
        for operation in (CodexRuntime.run_review, ClaudeCodeRuntime.run_review):
            with self.subTest(operation=operation.__qualname__):
                self.assertIn("workspace_only_command(", inspect.getsource(operation))
        pi_source = inspect.getsource(PiRuntime._run)
        self.assertIn("prepare_trace_command(", pi_source)
        self.assertIn("workspace_only_command(", pi_source)

    def test_seed_commands_use_only_moonshiner_owned_writable_storage(self):
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
        scratch = common.verify_scratch(workspace)
        home = scratch / "tmp" / ".sandbox-home"
        self.assertEqual("home", (home / "home-write").read_text())
        self.assertEqual("tmp", (scratch / "tmp" / "tmp-write").read_text())
        self.assertEqual("var-tmp",
                         (scratch / "tmp" / "var-tmp-write").read_text())
        self.assertEqual("shm", (scratch / "shm" / "shm-write").read_text())
        self.assertTrue(scratch.resolve().is_relative_to(
            common.VERIFY_HOMES.resolve()))

    def test_a_verify_command_sees_nothing_of_the_harness_in_its_workspace(self):
        """A seed's verifier judges the working tree; ours must not be in it.

        Many seeds accept only a tree holding their own files and nothing else.
        The verify sandbox used to make its HOME at `<workspace>/.sandbox-home`,
        so those seeds read the harness's scratch directory as the agent's
        litter and failed however good the patch was -- 152 of them at once.
        """
        workspace = common.WORKSPACES / f"confinement-clean-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        self.addCleanup(common.remove_workspace, workspace)
        (workspace / "seed-file.txt").write_text("mine\n")
        script = ("import os,pathlib\n"
                  "pathlib.Path(os.environ['HOME'],'noise').write_text('x')\n"
                  "print('\\n'.join(sorted(os.listdir('/srv'))))\n")
        result = common._sandboxed_command(
            ["/usr/bin/python3", "-c", script], workspace, 30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["seed-file.txt"], result.stdout.split())
        self.assertEqual(["seed-file.txt"],
                         sorted(path.name for path in workspace.iterdir()))

    def test_a_removed_workspace_takes_its_verify_state_with_it(self):
        workspace = common.WORKSPACES / f"confinement-paired-{uuid.uuid4().hex}"
        workspace.mkdir(parents=True)
        scratch = common.verify_scratch(workspace)
        (scratch / "tmp").mkdir(parents=True)
        (scratch / "tmp" / "cached").write_text("package\n")
        common.remove_workspace(workspace)
        self.assertFalse(workspace.exists())
        self.assertFalse(scratch.exists())

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

"""Real Podman contracts for Moonshiner's canonical OCI task environment."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import generate_traces  # noqa: E402
import task_environment  # noqa: E402
from runtimes import TraceHarnessInfrastructureFailure  # noqa: E402
from runtimes.base import ReviewResult, Runtime, TraceResult  # noqa: E402
from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402
from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.pi import PiRuntime  # noqa: E402


TEST_TMP = pathlib.Path(os.environ.get(
    "TMPDIR", ROOT / ".moonshiner" / "test-tmp")).resolve()
TEST_TMP.mkdir(parents=True, exist_ok=True)


class ExecutableOciRuntime(Runtime):
    name = "executable-oci-contract"
    trace_formats = ("codex-exec-events",)

    def preflight(self, *, require_auth: bool = False) -> None:
        return None

    def run_trace(self, seed: dict, workspace: pathlib.Path, *,
                  out_dir: pathlib.Path, system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        environment = self.teacher_environment(workspace)
        script = (
            "import pathlib,sys\n"
            "pathlib.Path('prompt.bin').write_bytes(sys.stdin.buffer.read())\n"
            "pathlib.Path('value.py').write_text('VALUE = \\\"fixed\\\"\\n')\n")
        command = self.prepare_trace_command(
            seed, ["python3", "-c", script], workspace,
            environment=environment)
        result = subprocess.run(command, cwd=workspace, env=environment,
                                input=prompt.encode(), capture_output=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = out_dir / f"{seed['id']}.events.jsonl"
        raw.write_text(
            '{"type":"thread.started","thread_id":"oci-contract"}\n'
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"completed"}}\n'
            '{"type":"turn.completed","usage":{}}\n')
        return TraceResult(
            raw_path=raw, trace_format=self.trace_formats[0],
            return_code=result.returncode, stream_success=result.returncode == 0,
            observed_model="contract-model", observed_models=["contract-model"],
            model_attested=True,
            error=result.stderr.decode(errors="replace") or None)

    def run_review(self, instruction: str, workspace: pathlib.Path, *,
                   out_dir: pathlib.Path, schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        return ReviewResult(raw_text="{}", verdict={}, return_code=0)

    @staticmethod
    def parse_stream(path: pathlib.Path, workspace: str | None):
        return [{"role": "assistant", "content": "completed"}], {}


@unittest.skipUnless(shutil.which("podman"), "Podman is required")
class RealOciTaskEnvironment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = TEST_TMP / f"oci-task-environment-{uuid.uuid4().hex}"
        cls.root.mkdir(parents=True)
        cls.old_root = task_environment.OCI_ROOT
        task_environment.OCI_ROOT = cls.root / "podman"
        context = cls.root / "image"
        context.mkdir()
        (context / "Containerfile").write_text("""
FROM registry.fedoraproject.org/fedora-toolbox:latest
RUN mkdir -p /testbed && cd /testbed && git init -q && \\
    git config user.email contract@example.com && \\
    git config user.name contract && \\
    printf 'VALUE = "broken"\\n' > value.py && \\
    git add value.py && \\
    GIT_AUTHOR_DATE=2020-01-01T00:00:00Z \\
    GIT_COMMITTER_DATE=2020-01-01T00:00:00Z \\
    git commit -qm baseline
WORKDIR /testbed
""".lstrip())
        cls.image = "localhost/moonshiner-oci-contract:latest"
        subprocess.run(task_environment.podman_command(
            "build", "-t", cls.image, str(context)), check=True,
            cwd=cls.root, capture_output=True, text=True)
        cls.base_commit = subprocess.run(task_environment.podman_command(
            "run", "--rm", "--network", "none", cls.image,
            "git", "-C", "/testbed", "rev-parse", "HEAD"), check=True,
            capture_output=True, text=True).stdout.strip()

    @classmethod
    def tearDownClass(cls):
        subprocess.run(task_environment.podman_command(
            "system", "reset", "--force"), capture_output=True, text=True)
        task_environment.OCI_ROOT = cls.old_root
        shutil.rmtree(cls.root, ignore_errors=True)

    def seed(self, name: str = "oci-contract") -> dict:
        directory = self.root / "seeds" / name
        patch = directory / "files" / ".moonshiner" / "test.patch"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text("""diff --git a/test_task.py b/test_task.py
new file mode 100644
index 0000000..d2b98ef
--- /dev/null
+++ b/test_task.py
@@ -0,0 +1,5 @@
+import unittest
+import value
+class Contract(unittest.TestCase):
+    def test_fix(self):
+        self.assertEqual(value.VALUE, "fixed")
""")
        task = {
            "id": name, "lang": "python", "category": "bug-fix",
            "program": "NVIDIA Open-SWE 10K", "prompt": "Fix value.py\r\n",
            "verify_cmd": "python3 -m unittest -q",
            "environment": {
                "type": "oci", "image": self.image,
                "repository": "owner/repository",
                "base_commit": self.base_commit, "workspace": "/testbed",
                "test_patch": "files/.moonshiner/test.patch",
                "fail_to_pass": ["Contract.test_fix"], "pass_to_pass": [],
                "install_config": {
                    "base_image_name": "fedora", "docker_specs": None,
                    "install": [], "log_parser": "parse_log_unittest",
                    "test_cmd": "python3 -m unittest -q",
                },
            },
            "provenance": {"manifest_id": "contract"},
        }
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task.json").write_text(json.dumps(task))
        task["_dir"] = directory
        return task

    def test_real_image_materializes_and_verifies_a_real_candidate(self):
        seed = self.seed()
        task_environment.prepare_environment(seed)
        workspace = common.materialize(seed, name="oci-contract-workspace")
        self.addCleanup(common.remove_workspace, workspace)
        self.assertEqual((workspace / "value.py").read_text(), 'VALUE = "broken"\n')
        baseline, _ = common.run_verify(seed, workspace)
        self.assertFalse(baseline)
        (workspace / "value.py").write_text('VALUE = "fixed"\n')
        passed, output = common.run_verify(seed, workspace)
        self.assertTrue(passed, output)
        self.assertFalse((workspace / "test_task.py").exists())

    def test_invalid_image_is_infrastructure_failure(self):
        seed = self.seed("invalid-image")
        seed["environment"]["image"] = "not a valid image reference"
        with self.assertRaises(TraceHarnessInfrastructureFailure):
            task_environment.prepare_environment(seed)

    def test_trace_task_passes_prompt_bytes_unchanged_to_run_trace(self):
        seed = self.seed("prompt-byte-contract")
        prompt = "First line\r\nUnicode \u2028 and trailing newline\n"
        seed["prompt"] = prompt
        runtime = ExecutableOciRuntime(
            {"runtimes": {ExecutableOciRuntime.name: {}}},
            {"runtime": ExecutableOciRuntime.name, "model": "contract-model"})
        traces = self.root / "traces"
        record = generate_traces.trace_task(
            seed, runtime, force=True, traces_root=traces,
            capability_resolution={"mode": "contract"})
        workspace = pathlib.Path(record["_workspace_path"])
        self.addCleanup(common.remove_workspace, workspace)
        self.assertEqual((workspace / "prompt.bin").read_bytes(), prompt.encode())
        self.assertTrue(record["passed"], record.get("verify_output"))

    def test_every_native_adapter_executable_starts_in_the_same_oci_boundary(self):
        seed = self.seed("native-adapter-probes")
        task_environment.prepare_environment(seed)
        workspace = common.materialize(
            seed, name=f"native-adapter-probes-{uuid.uuid4().hex}")
        self.addCleanup(common.remove_workspace, workspace)
        config = {
            "workspace": {"confirmed_root": str(ROOT)},
            "runtimes": {
                "codex": {"cli": "codex"},
                "claude-code": {"cli": "claude"},
                "pi": {"cli": "node_modules/.bin/pi"},
            },
        }
        adapters = []
        if shutil.which("codex"):
            adapters.append(CodexRuntime(
                config, {"runtime": "codex", "model": "probe"}))
        if shutil.which("claude"):
            adapters.append(ClaudeCodeRuntime(
                config, {"runtime": "claude-code", "model": "probe"}))
        if shutil.which("node") and (ROOT / "node_modules/.bin/pi").exists():
            adapters.append(PiRuntime(
                config, {"runtime": "pi", "model": "probe"}))
        if not adapters:
            self.skipTest("no native harness CLI is installed")
        environment = Runtime.teacher_environment(workspace)
        for adapter in adapters:
            with self.subTest(adapter=adapter.name):
                task_environment.probe_runtime(
                    seed, adapter, workspace, environment)


if __name__ == "__main__":
    unittest.main()

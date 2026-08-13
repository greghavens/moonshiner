"""Harness work is killed for sustained inactivity, never total runtime."""
import ast
import inspect
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runtimes import base  # noqa: E402
from runtimes.claude_code import ClaudeCodeRuntime  # noqa: E402
from runtimes.codex import CodexRuntime  # noqa: E402
from runtimes.pi import run_streamed  # noqa: E402
import security_runtime  # noqa: E402


class InactivityTimeout(unittest.TestCase):
    def run_command(self, source, *, timeout=0.12, workspace=None):
        return base.run_with_inactivity_timeout(
            [sys.executable, "-u", "-c", source],
            inactivity_timeout=timeout,
            cwd=workspace,
            text=True,
            capture_output=True,
        )

    def test_stdout_progress_may_outlive_the_timeout(self):
        result = self.run_command(
            "import time\n"
            "for i in range(8):\n"
            " print(i, flush=True)\n"
            " time.sleep(0.04)\n")
        self.assertEqual(0, result.returncode)
        self.assertEqual(8, len(result.stdout.splitlines()))

    def test_stderr_progress_may_outlive_the_timeout(self):
        result = self.run_command(
            "import sys,time\n"
            "for i in range(8):\n"
            " print(i, file=sys.stderr, flush=True)\n"
            " time.sleep(0.04)\n")
        self.assertEqual(0, result.returncode)
        self.assertEqual(8, len(result.stderr.splitlines()))

    def test_cpu_progress_may_outlive_the_timeout_without_output(self):
        result = self.run_command(
            "import time\n"
            "end=time.monotonic()+0.35\n"
            "while time.monotonic()<end: pass\n")
        self.assertEqual(0, result.returncode)

    def test_disk_progress_may_outlive_the_timeout_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_command(
                "import os,time\n"
                "with open('progress','wb',buffering=0) as f:\n"
                " for i in range(8):\n"
                "  f.write(b'x'*65536); os.fsync(f.fileno()); time.sleep(0.04)\n",
                workspace=directory)
        self.assertEqual(0, result.returncode)

    def test_active_descendant_keeps_a_silent_parent_alive(self):
        result = self.run_command(
            "import subprocess,sys\n"
            "subprocess.run([sys.executable,'-u','-c',"
            "'import time; end=time.monotonic()+0.35; "
            "exec(\"while time.monotonic()<end: pass\")'])\n")
        self.assertEqual(0, result.returncode)

    def test_continuously_inactive_process_tree_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = pathlib.Path(directory) / "child.pid"
            source = (
                "import pathlib,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
                "time.sleep(60)\n")
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_command(source, timeout=0.12, workspace=directory)
            self.assertLess(time.monotonic() - started, 2)
            child_pid = int(pidfile.read_text())
            for _ in range(50):
                if not pathlib.Path(f"/proc/{child_pid}").exists():
                    break
                time.sleep(0.01)
            self.assertFalse(pathlib.Path(f"/proc/{child_pid}").exists(),
                             "the inactive descendant survived the timeout")


class OneSharedRuntimeBoundary(unittest.TestCase):
    def test_all_harness_processes_use_the_inactivity_runner(self):
        for operation in (
                CodexRuntime.run_trace, CodexRuntime.run_review,
                ClaudeCodeRuntime.run_trace, ClaudeCodeRuntime.run_review,
                run_streamed):
            with self.subTest(operation=operation.__qualname__):
                source = inspect.getsource(operation)
                self.assertIn("run_with_inactivity_timeout(", source)
                self.assertNotIn("subprocess.run(", source)

        security_source = inspect.getsource(security_runtime.run_codex)
        self.assertIn("wait_with_inactivity_timeout(", security_source)
        self.assertNotIn("proc.wait(timeout=timeout_s)", security_source)

    def test_no_process_uses_subprocess_run_total_runtime_timeout(self):
        offenders = []
        for path in (ROOT / "src").glob("**/*.py"):
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if not (isinstance(function, ast.Attribute)
                        and isinstance(function.value, ast.Name)
                        and function.value.id == "subprocess"
                        and function.attr == "run"):
                    continue
                if any(keyword.arg == "timeout" for keyword in node.keywords):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()

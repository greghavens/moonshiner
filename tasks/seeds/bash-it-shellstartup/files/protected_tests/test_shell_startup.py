from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


class ShellStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        if BASH is None:
            self.fail("bash is required for this fixture")

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name) / "home"
        (self.home / ".local" / "bin").mkdir(parents=True)

        for relative in (".bashrc", ".bash_profile", ".local/bin/project-context"):
            destination = self.home / relative
            shutil.copy2(ROOT / relative, destination)

        self.trace = Path(self.tempdir.name) / "context.trace"

    def run_bash(
        self,
        *arguments: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "BASH_ENV",
            "ENV",
            "BASH_IT_PROJECT_CONTEXT",
            "PROJECT_CONTEXT_INITIALIZED",
            "PROJECT_CONTEXT_LOADS",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "HOME": str(self.home),
                "BASH_IT_CONTEXT_TRACE": str(self.trace),
                "LC_ALL": "C",
            }
        )
        if extra_env:
            environment.update(extra_env)

        return subprocess.run(
            [BASH, *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

    def trace_lines(self) -> list[str]:
        if not self.trace.exists():
            return []
        return self.trace.read_text(encoding="utf-8").splitlines()

    def test_interactive_login_sources_bashrc_and_initializes_once(self) -> None:
        result = self.run_bash(
            "--noprofile",
            "--norc",
            "--login",
            "-i",
            "-c",
            'source "$HOME/.bash_profile"; '
            'printf "%s|%s|%s|%s\\n" "$BASH_PROFILE_LOADED" '
            '"$BASH_IT_READY" "$PROJECT_CONTEXT_INITIALIZED" "$PROJECT_CONTEXT_LOADS"',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1|1|1|1\n")
        self.assertEqual(self.trace_lines(), ["project-context shell-init bash"])

    def test_interactive_nonlogin_initializes_once(self) -> None:
        result = self.run_bash(
            "--noprofile",
            "--norc",
            "-i",
            "-c",
            'source "$HOME/.bashrc"; '
            'printf "%s|%s|%s\\n" "$BASH_IT_READY" '
            '"$PROJECT_CONTEXT_INITIALIZED" "$PROJECT_CONTEXT_LOADS"',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "1|1|1\n")
        self.assertEqual(self.trace_lines(), ["project-context shell-init bash"])

    def test_noninteractive_bash_env_does_not_run_optional_helper(self) -> None:
        result = self.run_bash(
            "--noprofile",
            "--norc",
            "-c",
            'printf "payload:%s\\n" "${PROJECT_CONTEXT_INITIALIZED-unset}"',
            extra_env={"BASH_ENV": str(self.home / ".bashrc")},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "payload:unset\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.trace_lines(), [])

    def test_opt_out_is_reversible_and_keeps_user_startup_content(self) -> None:
        result = self.run_bash(
            "--noprofile",
            "--norc",
            "-i",
            "-c",
            'source "$HOME/.bashrc"; '
            'printf "%s|%s\\n" "${PROJECT_CONTEXT_INITIALIZED-unset}" "$BASH_IT_READY"',
            extra_env={"BASH_IT_PROJECT_CONTEXT": "off"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "unset|1\n")
        self.assertEqual(self.trace_lines(), [])

    def test_missing_optional_helper_is_quiet_and_startup_continues(self) -> None:
        (self.home / ".local" / "bin" / "project-context").unlink()
        result = self.run_bash(
            "--noprofile",
            "--norc",
            "-i",
            "-c",
            'source "$HOME/.bashrc"; '
            'printf "%s|%s\\n" "${PROJECT_CONTEXT_INITIALIZED-unset}" "$BASH_IT_READY"',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "unset|1\n")
        self.assertNotIn("project-context", result.stderr)
        self.assertEqual(self.trace_lines(), [])

    def test_managed_stanza_is_preserved(self) -> None:
        bashrc = (ROOT / ".bashrc").read_text(encoding="utf-8")
        self.assertIn("# >>> project-context managed stanza >>>", bashrc)
        self.assertIn("# <<< project-context managed stanza <<<", bashrc)
        self.assertIn(".local/bin/project-context", bashrc)
        self.assertIn("shell-init bash", bashrc)


if __name__ == "__main__":
    unittest.main()

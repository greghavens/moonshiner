import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "atomic_file.c"


class TemporaryFileSecurityTests(unittest.TestCase):
    def test_source_uses_the_posix_exclusive_tempfile_contract(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertTrue(
            source.startswith("#define _POSIX_C_SOURCE 200809L\n"),
            "the POSIX.1-2008 declarations must be enabled before system headers",
        )
        self.assertRegex(
            source,
            r"\bmkstemp\s*\(",
            "temporary files must be created atomically and exclusively with mkstemp",
        )
        self.assertRegex(
            source,
            r"\.tmp\.X{6}",
            "mkstemp must receive a mutable template ending in six X characters",
        )
        self.assertNotRegex(source, r"\bgetpid\s*\(", "PID-derived names remain predictable")
        self.assertNotRegex(
            source,
            r"\b(?:mktemp|tempnam|tmpnam)\s*\(",
            "an insecure temporary-name API is still in use",
        )
        self.assertNotRegex(
            source,
            r"(?:\bO_TMPFILE\b|\bopenat2\s*\(|\b_GNU_SOURCE\b)",
            "the repair must not depend on Linux-specific interfaces",
        )
        for operation in ("fchmod", "fsync", "rename", "unlink"):
            self.assertRegex(
                source,
                rf"\b{operation}\s*\(",
                f"the existing {operation} contract must be preserved",
            )

    def test_strict_c17_build_and_runtime_contract(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            executable = Path(temporary_directory) / "runtime_test"
            compile_result = subprocess.run(
                [
                    "cc",
                    "-std=c17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wpedantic",
                    "-Wconversion",
                    "-Wshadow",
                    "-Wstrict-prototypes",
                    "-Iinclude",
                    "src/atomic_file.c",
                    "tests/runtime_test.c",
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(
                0,
                compile_result.returncode,
                f"strict C17 compilation failed:\n{compile_result.stdout}{compile_result.stderr}",
            )

            run_result = subprocess.run(
                [str(executable), temporary_directory],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(
                0,
                run_result.returncode,
                f"runtime contract failed:\n{run_result.stdout}{run_result.stderr}",
            )
            self.assertEqual("all runtime checks passed\n", run_result.stdout)
            self.assertEqual("", run_result.stderr)


if __name__ == "__main__":
    unittest.main()

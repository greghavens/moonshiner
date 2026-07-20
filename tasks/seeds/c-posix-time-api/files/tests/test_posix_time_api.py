import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "event_time.c"


class PosixTimeApiTests(unittest.TestCase):
    def test_production_uses_the_posix_reentrant_contract(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertRegex(source, r"\bgmtime_r\s*\(", "the POSIX gmtime_r API is not used")
        self.assertNotRegex(source, r"\bgmtime_s\s*\(", "the nonportable gmtime_s call remains")
        self.assertNotRegex(source, r"(?<![_a-zA-Z0-9])gmtime\s*\(", "gmtime is not thread-safe")

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
                    "-pthread",
                    "-Iinclude",
                    "src/event_time.c",
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
                [str(executable)],
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

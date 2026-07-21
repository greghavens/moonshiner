import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MmioCommandTests(unittest.TestCase):
    def test_strict_c17_runtime_and_access_log(self):
        compiler = os.environ.get("CC", "cc")
        with tempfile.TemporaryDirectory(prefix="mmio-command-test-") as directory:
            executable = Path(directory) / "runtime_test"
            build = subprocess.run(
                [
                    compiler,
                    "-std=c17",
                    "-O2",
                    "-Wall",
                    "-Wextra",
                    "-Wpedantic",
                    "-Wconversion",
                    "-Werror",
                    "-I",
                    str(ROOT / "include"),
                    str(ROOT / "src" / "mmio_command.c"),
                    str(ROOT / "tests" / "runtime_test.c"),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                build.returncode,
                0,
                msg=f"strict C17 build failed:\n{build.stdout}{build.stderr}",
            )

            run = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                run.returncode,
                0,
                msg=f"runtime fixture failed:\n{run.stdout}{run.stderr}",
            )

    def test_exact_width_access_contract_is_retained(self):
        header = (ROOT / "include" / "mmio_command.h").read_text(encoding="utf-8")
        source = (ROOT / "src" / "mmio_command.c").read_text(encoding="utf-8")

        self.assertRegex(
            header,
            r"void\s*\(\*write32\)\s*\([^;]*uint32_t\s+value\s*\)\s*;",
        )
        self.assertRegex(
            header,
            r"void\s*\(\*write16\)\s*\([^;]*uint16_t\s+value\s*\)\s*;",
        )
        self.assertRegex(
            header,
            r"uint16_t\s*\(\*read16\)\s*\([^;]*\)\s*;",
        )
        self.assertRegex(
            header,
            r"uint8_t\s*\(\*read8\)\s*\([^;]*\)\s*;",
        )
        self.assertNotRegex(source, r"\bvolatile\b")
        self.assertNotRegex(source, r"\*\s*\([^)]*MMIO_")

    def test_strict_flags_are_pinned(self):
        own_source = Path(__file__).read_text(encoding="utf-8")

        for flag in (
            '"-std=c17"',
            '"-Wall"',
            '"-Wextra"',
            '"-Wpedantic"',
            '"-Wconversion"',
            '"-Werror"',
        ):
            self.assertIn(flag, own_source)


if __name__ == "__main__":
    unittest.main()

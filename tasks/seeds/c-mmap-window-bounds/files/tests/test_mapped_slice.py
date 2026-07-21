import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MappedSliceTests(unittest.TestCase):
    def test_strict_c17_runtime(self):
        compiler = os.environ.get("CC", "cc")
        with tempfile.TemporaryDirectory(prefix="mapped-slice-test-") as directory:
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
                    "-Wshadow",
                    "-Werror",
                    "-I",
                    str(ROOT / "include"),
                    str(ROOT / "src" / "mapped_slice.c"),
                    str(ROOT / "tests" / "runtime_test.c"),
                    "-Wl,--wrap=open",
                    "-Wl,--wrap=close",
                    "-Wl,--wrap=mmap",
                    "-Wl,--wrap=munmap",
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

    def test_mapping_and_overflow_contract_is_retained(self):
        source = (ROOT / "src" / "mapped_slice.c").read_text(encoding="utf-8")

        self.assertRegex(source, r"\bopen\s*\([^;]*\bO_RDONLY\b")
        self.assertNotRegex(source, r"\bO_(?:WRONLY|RDWR)\b")
        self.assertRegex(
            source,
            r"\bmmap\s*\([^;]*\bPROT_READ\b[^;]*\bMAP_PRIVATE\b",
        )
        self.assertNotIn("PROT_WRITE", source)
        self.assertNotIn("MAP_SHARED", source)
        self.assertRegex(source, r"\bfile_size\s*-\s*requested_offset\b")
        self.assertGreaterEqual(source.count("SIZE_MAX"), 2)
        self.assertRegex(source, r"\bmunmap\s*\(")
        self.assertRegex(source, r"\bmemcpy\s*\(")

        forbidden_calls = ("pread", "read", "malloc", "calloc", "realloc")
        for name in forbidden_calls:
            self.assertNotRegex(source, rf"\b{re.escape(name)}\s*\(")


if __name__ == "__main__":
    unittest.main()

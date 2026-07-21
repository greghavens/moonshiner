import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DmaRingTests(unittest.TestCase):
    def test_strict_c17_runtime(self):
        compiler = os.environ.get("CC", "cc")
        with tempfile.TemporaryDirectory(prefix="dma-ring-test-") as directory:
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
                    str(ROOT / "src" / "dma_ring.c"),
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

    def test_mmio_and_memory_order_contract_is_retained(self):
        header = (ROOT / "include" / "dma_ring.h").read_text(encoding="utf-8")
        source = (ROOT / "src" / "dma_ring.c").read_text(encoding="utf-8")

        self.assertRegex(header, r"volatile\s+uint32_t\s+producer\s*;")
        self.assertRegex(header, r"volatile\s+uint32_t\s+consumer\s*;")
        self.assertGreaterEqual(
            len(re.findall(r"atomic_thread_fence\s*\(\s*memory_order_acquire\s*\)", source)),
            1,
        )
        self.assertGreaterEqual(
            len(re.findall(r"atomic_thread_fence\s*\(\s*memory_order_release\s*\)", source)),
            2,
        )
        self.assertNotIn("memcpy", source)
        self.assertNotIn("malloc", source)


if __name__ == "__main__":
    unittest.main()

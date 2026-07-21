import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EventQueueTests(unittest.TestCase):
    def test_strict_c17_runtime(self):
        compiler = os.environ.get("CC", "cc")
        with tempfile.TemporaryDirectory(prefix="event-queue-test-") as directory:
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
                    str(ROOT / "src" / "event_queue.c"),
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

    def test_bounded_lock_free_contract_is_retained(self):
        header = (ROOT / "include" / "event_queue.h").read_text(encoding="utf-8")
        source = (ROOT / "src" / "event_queue.c").read_text(encoding="utf-8")

        self.assertRegex(
            header,
            r"slots\s*\[\s*EVENT_QUEUE_CAPACITY\s*\]\s*;",
        )
        self.assertRegex(header, r"_Atomic\s+unsigned\s+int\s+count\s*;")
        self.assertRegex(header, r"_Atomic\s+unsigned\s+int\s+dropped\s*;")
        self.assertRegex(
            source,
            r"_Static_assert\s*\(\s*ATOMIC_INT_LOCK_FREE\s*==\s*2\s*,",
        )
        self.assertGreaterEqual(
            len(re.findall(r"memory_order_acquire", source)),
            3,
        )
        self.assertGreaterEqual(
            len(re.findall(r"memory_order_release", source)),
            2,
        )
        self.assertRegex(
            source,
            r"atomic_fetch_sub_explicit\s*\(\s*&queue->count\s*,\s*1U\s*,"
            r"\s*memory_order_release\s*\)",
        )
        self.assertNotRegex(
            source,
            r"atomic_store_explicit\s*\(\s*&queue->count\s*,",
        )

        forbidden = (
            "malloc",
            "calloc",
            "realloc",
            "free(",
            "pthread",
            "mutex",
            "mtx_",
            "atomic_flag",
            "disable_irq",
            "critical_section",
        )
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()

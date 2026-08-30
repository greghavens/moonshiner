#!/usr/bin/env python3
"""Compile and run the protected, offline Java acceptance harness."""

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vcfarch-0097-") as build_dir:
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "--release",
                "17",
                "-d",
                build_dir,
                str(ROOT / "VcfMigrationPlanner.java"),
                str(ROOT / "tests" / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        if compile_result.returncode:
            sys.stderr.write(compile_result.stdout)
            sys.stderr.write(compile_result.stderr)
            return compile_result.returncode

        run_result = subprocess.run(
            ["java", "-cp", build_dir, "TestMain", str(ROOT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        sys.stdout.write(run_result.stdout)
        sys.stderr.write(run_result.stderr)
        return run_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

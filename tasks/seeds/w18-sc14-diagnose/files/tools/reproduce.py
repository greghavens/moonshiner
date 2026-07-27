#!/usr/bin/env python3

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent


def main():
    compiler = shutil.which("cc")
    if compiler is None:
        print("reproduce: C compiler unavailable", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="boot-trial-repro-") as raw:
        executable = Path(raw) / "reproduce"
        build = subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wpedantic",
                "-I",
                str(ROOT / "include"),
                str(ROOT / "src" / "boot_trial.c"),
                str(ROOT / "tools" / "repro_main.c"),
                "-o",
                str(executable),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if build.returncode != 0:
            print(build.stdout, end="", file=sys.stderr)
            print(build.stderr, end="", file=sys.stderr)
            return build.returncode

        run = subprocess.run([str(executable)], cwd=ROOT)
        return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())

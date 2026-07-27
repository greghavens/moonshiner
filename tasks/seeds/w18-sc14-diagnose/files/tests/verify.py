#!/usr/bin/env python3

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
COMMON = [
    "-std=c11",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-Wpedantic",
    "-Wvla",
]


def run(command):
    try:
        process = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: command timed out", file=sys.stderr)
        raise SystemExit(1)
    if process.returncode != 0:
        print(
            "FAIL: " + " ".join(str(part) for part in command),
            file=sys.stderr,
        )
        if process.stdout:
            print(process.stdout, end="", file=sys.stderr)
        if process.stderr:
            print(process.stderr, end="", file=sys.stderr)
        raise SystemExit(1)
    return process.stdout


def check_source_policy():
    source = (ROOT / "src" / "boot_trial.c").read_text(
        encoding="utf-8"
    )
    forbidden = re.compile(
        r"\b(?:malloc|calloc|realloc|aligned_alloc|free|"
        r"pthread_create|sleep|usleep|nanosleep|system)\s*\("
    )
    match = forbidden.search(source)
    if match:
        print(
            "FAIL: forbidden unbounded/runtime facility: "
            + match.group(0),
            file=sys.stderr,
        )
        raise SystemExit(1)


def main():
    compiler = shutil.which("cc")
    nm = shutil.which("nm")
    if compiler is None or nm is None:
        print("FAIL: required C build tools unavailable", file=sys.stderr)
        return 1

    check_source_policy()
    with tempfile.TemporaryDirectory(prefix="boot-trial-tests-") as raw:
        build = Path(raw)
        include = ["-I", str(ROOT / "include")]

        abi_probe = build / "abi_probe"
        run([
            compiler,
            *COMMON,
            *include,
            str(ROOT / "src" / "boot_trial.c"),
            str(ROOT / "tests" / "abi_probe.c"),
            "-o",
            str(abi_probe),
        ])
        run([str(abi_probe)])

        behavior = build / "test_boot_trial"
        run([
            compiler,
            *COMMON,
            *include,
            str(ROOT / "src" / "boot_trial.c"),
            str(ROOT / "tests" / "test_boot_trial.c"),
            "-o",
            str(behavior),
        ])
        output = run([str(behavior)])

        library = build / "libboot_trial.so"
        run([
            compiler,
            *COMMON,
            "-fPIC",
            "-shared",
            *include,
            str(ROOT / "src" / "boot_trial.c"),
            "-o",
            str(library),
        ])
        symbols = run([
            nm,
            "-D",
            "--defined-only",
            str(library),
        ]).splitlines()
        exported = {
            line.split()[-1] for line in symbols if line.split()
        }
        required = {"boot_trial_begin", "boot_trial_poll"}
        if not required.issubset(exported):
            print(
                "FAIL: deployed C symbols are missing",
                file=sys.stderr,
            )
            return 1

    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

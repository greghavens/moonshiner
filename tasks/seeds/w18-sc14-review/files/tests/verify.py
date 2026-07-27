#!/usr/bin/env python3
"""Deterministic offline verification for the boot-manifest gate."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
CC = shutil.which("cc") or shutil.which("gcc")
NM = shutil.which("nm")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def main() -> int:
    if CC is None or NM is None:
        print("required local C toolchain not found", file=sys.stderr)
        return 2

    BUILD.mkdir(exist_ok=True)
    common = [
        CC,
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-pedantic",
        "-Iinclude",
    ]

    source_object = BUILD / "boot_accept.o"
    run(common + ["-c", "src/boot_accept.c", "-o", str(source_object)])

    defined = run([NM, "-g", "--defined-only", str(source_object)]).stdout
    exported = []
    for line in defined.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-2].upper() in {"T", "D", "B", "R"}:
            exported.append(fields[-1])
    if exported != ["ba_stage"]:
        print(f"unexpected public symbols: {exported}", file=sys.stderr)
        return 1

    undefined = run([NM, "-u", str(source_object)]).stdout
    imported = {line.split()[-1].split("@", 1)[0] for line in undefined.splitlines()
                if line.split()}
    allowed_imports = {"memcmp", "memcpy"}
    unexpected_imports = sorted(imported - allowed_imports)
    if unexpected_imports:
        print(f"unexpected runtime imports: {unexpected_imports}",
              file=sys.stderr)
        return 1

    abi_probe = BUILD / "abi_probe"
    run(common + ["tests/abi_probe.c", "src/boot_accept.c",
                  "-o", str(abi_probe)])
    run([str(abi_probe)])

    test_binary = BUILD / "test_boot_accept"
    run(common + ["tests/test_boot_accept.c", "src/boot_accept.c",
                  "-o", str(test_binary)])
    result = run([str(test_binary)])
    print(result.stdout, end="")
    print("object ABI/import checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

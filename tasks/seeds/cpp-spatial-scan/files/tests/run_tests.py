#!/usr/bin/env python3
"""Build and run the protected C++ test harness without external dependencies."""

from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    compiler = next(
        (
            candidate
            for candidate in (
                "c++",
                "g++",
                "clang++",
                "/home/linuxbrew/.linuxbrew/bin/clang++",
            )
            if shutil.which(candidate) is not None
        ),
        None,
    )
    if compiler is None:
        raise RuntimeError("a C++20 compiler is required")
    compiler_flags: list[str] = []
    compiler_path = Path(shutil.which(compiler) or compiler).resolve()
    llvm_root = compiler_path.parents[1]
    if (llvm_root / "include" / "c++" / "v1").is_dir():
        compiler_flags = [
            "-stdlib=libc++",
            f"-L{llvm_root / 'lib'}",
            f"-Wl,-rpath,{llvm_root / 'lib'}",
        ]

    binary = Path("/tmp/cpp-spatial-scan-proximity-tests")
    compile_result = subprocess.run(
        [
            compiler,
            "-std=c++20",
            *compiler_flags,
            "-O2",
            "-Wall",
            "-Wextra",
            "-Wpedantic",
            "-Werror",
            "-I",
            str(ROOT / "include"),
            str(ROOT / "src" / "proximity.cpp"),
            str(ROOT / "tests" / "proximity_tests.cpp"),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=False,
    )
    if compile_result.returncode != 0:
        return compile_result.returncode
    return subprocess.run([str(binary)], cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

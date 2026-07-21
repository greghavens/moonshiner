#!/usr/bin/env python3
"""Assemble the compiled source sets into a reproducible multi-release JAR."""

from pathlib import Path
import sys
import zipfile


FIXED_TIME = (2020, 1, 1, 0, 0, 0)
MANIFEST = b"Manifest-Version: 1.0\r\nMulti-Release: true\r\n\r\n"


def files_below(root: Path):
    return sorted(path for path in root.rglob("*") if path.is_file())


def assemble(base_dir: Path, java11_dir: Path, resources_dir: Path, output: Path):
    entries = {"META-INF/MANIFEST.MF": MANIFEST}

    for path in files_below(base_dir):
        entries[path.relative_to(base_dir).as_posix()] = path.read_bytes()

    for path in files_below(resources_dir):
        entries[path.relative_to(resources_dir).as_posix()] = path.read_bytes()

    for path in files_below(java11_dir):
        arcname = path.relative_to(java11_dir).as_posix()
        entries[arcname] = path.read_bytes()

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as jar:
        for arcname in sorted(entries):
            info = zipfile.ZipInfo(arcname, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            jar.writestr(info, entries[arcname])


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: assemble.py BASE_CLASSES JAVA11_CLASSES RESOURCES OUTPUT"
        )
    assemble(*(Path(argument) for argument in sys.argv[1:]))

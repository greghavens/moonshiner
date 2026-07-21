#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
OBJECT_NAMES = ("checksum.o", "encode.o", "format.o")
EXPECTED_SYMBOLS = {
    "beacon_checksum",
    "beacon_encode_u32",
    "beacon_format_record",
}


def run(
    *args: object,
    cwd: Path = ROOT,
    text: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    command = [os.fspath(arg) for arg in args]
    environment = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    if extra_env:
        environment.update(extra_env)
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=text,
        env=environment,
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_objects(object_dir: Path) -> dict[str, str]:
    run("make", "objects", f"OBJDIR={object_dir}")
    return {name: digest(object_dir / name) for name in OBJECT_NAMES}


def set_completion_times(object_dir: Path, order: tuple[str, ...]) -> None:
    base = 2_000_000_000
    for position, name in enumerate(order):
        stamp = base + position * 10
        os.utime(object_dir / name, ns=(stamp * 1_000_000_000,) * 2)


def build_archive(
    object_dir: Path,
    archive: Path,
    ar_wrapper: Path,
    ar_environment: dict[str, str],
) -> None:
    run(
        "make",
        "archive",
        f"OBJDIR={object_dir}",
        f"LIBRARY={archive}",
        f"AR={ar_wrapper}",
        extra_env=ar_environment,
    )


def make_ar_wrapper(scratch: Path) -> tuple[Path, Path, dict[str, str]]:
    real_ar = shutil.which("ar")
    if real_ar is None:
        raise AssertionError("ar is required by this C build")
    wrapper = scratch / "recording-ar"
    log = scratch / "ar-invocations.log"
    wrapper.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf '%s\\n' \"$*\" >> \"$AR_WRAPPER_LOG\"\n"
        "exec \"$REAL_AR\" \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper, log, {"REAL_AR": real_ar, "AR_WRAPPER_LOG": os.fspath(log)}


def archive_members(archive: Path) -> list[str]:
    result = run("ar", "t", archive)
    return result.stdout.splitlines()


def regular_headers(archive: Path) -> dict[str, tuple[int, int, int]]:
    payload = archive.read_bytes()
    if not payload.startswith(b"!<arch>\n"):
        raise AssertionError("library is not a System V ar archive")

    headers: dict[str, tuple[int, int, int]] = {}
    offset = 8
    while offset < len(payload):
        header = payload[offset : offset + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise AssertionError("malformed ar member header")
        name = header[0:16].decode("ascii").strip().removesuffix("/")
        timestamp = int(header[16:28].decode("ascii").strip() or "0")
        owner = int(header[28:34].decode("ascii").strip() or "0")
        group = int(header[34:40].decode("ascii").strip() or "0")
        size = int(header[48:58].decode("ascii").strip())
        if name in OBJECT_NAMES:
            headers[name] = (timestamp, owner, group)
        offset += 60 + size + (size & 1)

    if set(headers) != set(OBJECT_NAMES):
        raise AssertionError(f"unexpected regular archive members: {sorted(headers)}")
    return headers


def archive_symbols(archive: Path) -> set[str]:
    output = run("nm", "-g", "--defined-only", archive).stdout
    return set(re.findall(r"\b[TDRB] (beacon_[A-Za-z0-9_]+)$", output, re.MULTILINE))


def assert_install_layout(archive: Path, object_dir: Path, scratch: Path) -> None:
    stage = scratch / "install-root"
    run(
        "make",
        "install",
        f"OBJDIR={object_dir}",
        f"LIBRARY={archive}",
        f"DESTDIR={stage}",
        "PREFIX=/opt/beacon",
    )
    installed = sorted(
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file()
    )
    expected = [
        "opt/beacon/include/beacon.h",
        "opt/beacon/lib/libbeacon.a",
    ]
    if installed != expected:
        raise AssertionError(f"install layout changed: {installed!r}")
    if (stage / expected[0]).read_bytes() != (ROOT / "include/beacon.h").read_bytes():
        raise AssertionError("installed public header changed")
    if (stage / expected[1]).read_bytes() != archive.read_bytes():
        raise AssertionError("installed library is not the built library")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix=".repro-test-", dir=ROOT) as temporary:
        scratch = Path(temporary)
        first_objects = scratch / "objects-first"
        second_objects = scratch / "objects-second"
        first_archive = scratch / "first" / "libbeacon.a"
        second_archive = scratch / "second" / "libbeacon.a"
        ar_wrapper, ar_log, ar_environment = make_ar_wrapper(scratch)

        first_hashes = compile_objects(first_objects)
        second_hashes = compile_objects(second_objects)
        if first_hashes != second_hashes:
            raise AssertionError("independent compilations changed object bytes")

        set_completion_times(first_objects, OBJECT_NAMES)
        set_completion_times(second_objects, tuple(reversed(OBJECT_NAMES)))
        before_archive = {
            path: digest(path)
            for directory in (first_objects, second_objects)
            for path in directory.glob("*.o")
        }

        build_archive(first_objects, first_archive, ar_wrapper, ar_environment)
        first_ar_calls = len(ar_log.read_text(encoding="utf-8").splitlines())
        if first_ar_calls == 0:
            raise AssertionError("first archive build did not honor the Makefile AR selection")

        build_archive(second_objects, second_archive, ar_wrapper, ar_environment)
        second_ar_calls = len(ar_log.read_text(encoding="utf-8").splitlines())
        if second_ar_calls <= first_ar_calls:
            raise AssertionError("second archive build did not honor the Makefile AR selection")

        if first_archive.read_bytes() != second_archive.read_bytes():
            raise AssertionError(
                "libbeacon.a is not reproducible across object mtimes/completion order"
            )
        if archive_members(first_archive) != list(OBJECT_NAMES):
            raise AssertionError("archive members are not in canonical filename order")
        if archive_members(second_archive) != list(OBJECT_NAMES):
            raise AssertionError("second archive members are not in canonical filename order")

        for archive in (first_archive, second_archive):
            for member, metadata in regular_headers(archive).items():
                if metadata != (0, 0, 0):
                    raise AssertionError(
                        f"archive metadata for {member} was not normalized: {metadata}"
                    )
            if archive_symbols(archive) != EXPECTED_SYMBOLS:
                raise AssertionError("exported library symbols changed")
            for name in OBJECT_NAMES:
                member = run("ar", "p", archive, name, text=False).stdout
                if hashlib.sha256(member).hexdigest() != first_hashes[name]:
                    raise AssertionError(f"archive changed object payload {name}")

        after_archive = {path: digest(path) for path in before_archive}
        if after_archive != before_archive:
            raise AssertionError("archiving modified an input object")

        assert_install_layout(first_archive, first_objects, scratch)

    print("reproducible archive, symbols, objects, and install layout: ok")


if __name__ == "__main__":
    main()

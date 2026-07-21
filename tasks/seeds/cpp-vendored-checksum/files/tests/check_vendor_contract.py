#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "cmake" / "ByteShieldLock.cmake"
VENDOR = ROOT / "vendor" / "byteshield"
PATCH = ROOT / "vendor" / "patches" / "0001-prefix-public-symbol.patch"
SOURCE_FILES = (
    "CMakeLists.txt",
    "include/byteshield/byteshield.h",
    "src/byteshield.c",
)

EXPECTED_VERSION = "2.3.0"
EXPECTED_SOURCE = "9e1069c41bbe4820f08b27177a29dc8a85869bbd81e48799b9679a8cef35e1ec"
EXPECTED_LICENSE = "dcca3abbeb35b79172c5a55148443216b63c77d45ba787009b350327e0e94fee"
EXPECTED_PATCH = "289802db75518c9c0d62cbc2cfe16836f4f8d4877f02838a1addfeae6f867a23"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest_digest() -> str:
    manifest = "".join(
        f"{relative}:{sha256(VENDOR / relative)}\n" for relative in SOURCE_FILES
    )
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def lock_settings(text: str) -> dict[str, str]:
    uncommented = re.sub(r"(?m)^[ \t]*#.*(?:\n|$)", "", text)
    command_pattern = re.compile(
        r"(?P<command>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<body>[^()]*)\)",
        re.S,
    )
    commands = list(command_pattern.finditer(uncommented))
    remainder = command_pattern.sub("", uncommented)
    if remainder.strip() or any(
        match.group("command").lower() != "set" for match in commands
    ):
        raise AssertionError("lock must contain data-only set commands")

    settings: dict[str, str] = {}
    for match in commands:
        assignment = re.fullmatch(
            r'\s*([A-Za-z_][A-Za-z0-9_]*)\s+"([^"]*)"\s*',
            match.group("body"),
            re.S,
        )
        if assignment is None:
            raise AssertionError("lock values must be quoted literals")
        name, value = assignment.groups()
        if name in settings:
            raise AssertionError(f"duplicate lock value: {name}")
        settings[name] = value
    return settings


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    lock = LOCK.read_text(encoding="utf-8")
    recipe = (ROOT / "cmake" / "VendorByteShield.cmake").read_text(
        encoding="utf-8"
    )
    source = (VENDOR / "src" / "byteshield.c").read_text(encoding="utf-8")
    header = (VENDOR / "include" / "byteshield" / "byteshield.h").read_text(
        encoding="utf-8"
    )
    settings = lock_settings(lock)

    require(source_manifest_digest() == EXPECTED_SOURCE,
            "protected ByteShield source fixture changed")
    require(sha256(VENDOR / "LICENSE") == EXPECTED_LICENSE,
            "protected ByteShield license changed")
    require(sha256(PATCH) == EXPECTED_PATCH,
            "protected ByteShield patch changed")

    expected_settings = {
        "STREAMSEAL_BYTESHIELD_VERSION": EXPECTED_VERSION,
        "STREAMSEAL_BYTESHIELD_SOURCE_SHA256": EXPECTED_SOURCE,
        "STREAMSEAL_BYTESHIELD_LICENSE_SHA256": EXPECTED_LICENSE,
        "STREAMSEAL_BYTESHIELD_PATCHES":
            f"0001-prefix-public-symbol.patch|{EXPECTED_PATCH}",
    }
    require(set(settings) == set(expected_settings),
            "lock must contain exactly the approved identity fields")
    require(settings["STREAMSEAL_BYTESHIELD_VERSION"] == EXPECTED_VERSION,
            "lock does not pin approved ByteShield 2.3.0")
    require(settings["STREAMSEAL_BYTESHIELD_SOURCE_SHA256"] == EXPECTED_SOURCE,
            "lock does not pin approved ByteShield source manifest")
    require(settings["STREAMSEAL_BYTESHIELD_LICENSE_SHA256"] == EXPECTED_LICENSE,
            "lock does not pin approved ByteShield license")
    require(
        settings["STREAMSEAL_BYTESHIELD_PATCHES"]
        == expected_settings["STREAMSEAL_BYTESHIELD_PATCHES"],
        "lock does not preserve the approved local patch digest",
    )

    require('#define BYTESHIELD_VERSION "2.3.0"' in header,
            "vendored header has wrong release identity")
    require("streamseal_vendor_byteshield_mix" in source,
            "local symbol-prefix patch is not present in staged source")
    require("-uint32_t byteshield_mix(" in PATCH.read_text(encoding="utf-8"),
            "local patch no longer records the upstream symbol")

    forbidden = ("FetchContent", "ExternalProject", "http://", "https://",
                 "curl", "wget", "git clone")
    require(not any(token in recipe for token in forbidden),
            "vendoring recipe contains a network/fetch path")
    require("file(SHA256" in recipe and "string(SHA256" in recipe,
            "vendoring recipe must verify file and manifest digests")
    require("add_subdirectory" in recipe and "EXCLUDE_FROM_ALL" in recipe,
            "vendored source must be added locally and excluded from all")
    for option in (
        "BYTESHIELD_BUILD_TESTS",
        "BYTESHIELD_BUILD_TOOLS",
        "BYTESHIELD_INSTALL",
        "BYTESHIELD_NATIVE_UNALIGNED",
    ):
        require(re.search(rf"set\({option}\s+OFF\s+CACHE\s+BOOL", recipe) is not None,
                f"vendoring recipe must force {option}=OFF")
    require('POSITION_INDEPENDENT_CODE ON' in recipe,
            "embedded objects must be position independent")
    require('C_VISIBILITY_PRESET hidden' in recipe,
            "embedded C symbols must have hidden visibility")

    print("vendor contract: ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"vendor contract: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)

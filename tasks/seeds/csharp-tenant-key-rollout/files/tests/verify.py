#!/usr/bin/env python3
"""Offline source-contract check for the focused completion-ordering defect."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "TenantKeyRollout" / "TenantKeyMigration.cs"


def method_body(source: str, signature: str) -> str:
    match = re.search(signature + r"\s*\{", source)
    if match is None:
        raise AssertionError("CompleteMigration must keep its public void API")

    opening_brace = match.end() - 1
    depth = 0
    state = "code"
    index = opening_brace
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if character == "/" and following == "/":
                state = "line-comment"
                index += 2
                continue
            if character == "/" and following == "*":
                state = "block-comment"
                index += 2
                continue
            if character == '"':
                state = "string"
            elif character == "'":
                state = "character"
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return source[opening_brace + 1:index]
        elif state == "line-comment":
            if character == "\n":
                state = "code"
        elif state == "block-comment":
            if character == "*" and following == "/":
                state = "code"
                index += 2
                continue
        elif state in {"string", "character"}:
            delimiter = '"' if state == "string" else "'"
            if character == "\\":
                index += 2
                continue
            if character == delimiter:
                state = "code"

        index += 1

    raise AssertionError("CompleteMigration has unbalanced braces")


def strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.DOTALL)


def verify_completion_order() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    body = strip_comments(method_body(
        source,
        r"public\s+void\s+CompleteMigration\s*\(\s*\)",
    ))

    inspection = re.search(
        r"\b(?:var|MigrationReadiness)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*"
        r"_store\s*\.\s*InspectMigrationReadiness\s*\(\s*\)\s*;",
        body,
    )
    if inspection is None:
        raise AssertionError("CompleteMigration must inspect migration readiness")

    name = re.escape(inspection.group("name"))
    compatibility = re.search(
        r"_store\s*\.\s*EnableLegacyWriterCompatibility\s*\(\s*\)\s*;",
        body,
    )
    guard = re.search(
        rf"if\s*\(\s*!\s*{name}\s*\.\s*IsReady\s*\)\s*\{{\s*"
        rf"throw\s+new\s+TenantKeyMigrationNotReadyException\s*\(\s*"
        rf"{name}\s*\.\s*MissingTenantKeyCount\s*\)\s*;\s*\}}",
        body,
    )
    activation = re.search(
        r"_store\s*\.\s*ActivateFinalTenantKeyConstraints\s*\(\s*\)\s*;",
        body,
    )

    if compatibility is None:
        raise AssertionError("CompleteMigration must close the legacy-writer path")
    if guard is None:
        raise AssertionError("incomplete migrations must fail before activation")
    if activation is None:
        raise AssertionError("ready migrations must activate the final constraints")

    if not (
        compatibility.start()
        < inspection.start()
        < guard.start()
        < activation.start()
    ):
        raise AssertionError(
            "legacy-writer compatibility must be enabled before readiness is "
            "inspected, and activation must remain after the readiness guard"
        )

    for operation in (
        "EnableLegacyWriterCompatibility",
        "InspectMigrationReadiness",
        "ActivateFinalTenantKeyConstraints",
    ):
        count = len(re.findall(rf"\b{operation}\s*\(", body))
        if count != 1:
            raise AssertionError(
                f"CompleteMigration must call {operation} exactly once; found {count}"
            )


def main() -> int:
    try:
        verify_completion_order()
    except (AssertionError, OSError) as error:
        print(f"FAIL completion closes the old-writer race: {error}", file=sys.stderr)
        return 1

    print("PASS completion closes the old-writer race")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

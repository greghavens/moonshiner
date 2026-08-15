#!/usr/bin/env python3
"""Deterministic protected verification for the knowledge-vault task."""

from __future__ import annotations

import difflib
import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "vault"
INBOX = ROOT / "inbox" / "captures.md"

INBOX_SHA256 = "5528c022ab34206ba7fe6326d541eeb7c31c96467156669c6c29d77d55045c55"

EXPECTED_NON_VAULT_FILES = {
    ".verification/verify.py",
    "inbox/captures.md",
}
EXPECTED_NON_VAULT_DIRECTORIES = {
    ".verification",
    "inbox",
}

EXPECTED = {
    "index.md": """# Knowledge Index

## Projects

- [August balcony supper](projects/august-balcony-supper.md)
- [Renew passport](projects/renew-passport.md)

## Areas

- [Window-box watering rhythm](areas/window-box-watering-rhythm.md)

## Resources

- [Neighborhood tree walk](resources/neighborhood-tree-walk.md)
- [Sourdough hydration cheat sheet](resources/sourdough-hydration-cheat-sheet.md)

## Archive

- [Sold film camera receipt](archive/sold-film-camera-receipt.md)
""",
    "projects/august-balcony-supper.md": """# August balcony supper

- Capture ID: `CP-101`
- Status: active
- Captured: 2026-08-11

## Source

> Invite Mara and Jo for dinner on the balcony.
> Test the folding table layout before Friday.

## Connections

- [Window-box watering rhythm](../areas/window-box-watering-rhythm.md)
""",
    "projects/renew-passport.md": """# Renew passport

- Capture ID: `CP-102`
- Status: next
- Captured: 2026-08-09

## Source

> Renew the passport before the autumn conference.
> Use the photo-booth picture in the desk drawer.

## Connections

- [Sold film camera receipt](../archive/sold-film-camera-receipt.md)
""",
    "areas/window-box-watering-rhythm.md": """# Window-box watering rhythm

- Capture ID: `CP-103`
- Status: ongoing
- Captured: 2026-08-07

## Source

> Check the window boxes every Tuesday and Saturday.
> Water only when the top inch of soil is dry.

## Connections

- [Sourdough hydration cheat sheet](../resources/sourdough-hydration-cheat-sheet.md)
""",
    "resources/sourdough-hydration-cheat-sheet.md": """# Sourdough hydration cheat sheet

- Capture ID: `CP-104`
- Status: reference
- Captured: 2026-07-28

## Source

> Country loaf: 750 g flour and 525 g water.
> Rye loaf: 600 g flour and 480 g water.

## Connections

- [August balcony supper](../projects/august-balcony-supper.md)
""",
    "archive/sold-film-camera-receipt.md": """# Sold film camera receipt

- Capture ID: `CP-105`
- Status: complete
- Captured: 2026-06-14

## Source

> Camera sold to N. Alvarez for $180.
> Receipt number: CAM-0614.

## Connections

- [Renew passport](../projects/renew-passport.md)
""",
    "resources/neighborhood-tree-walk.md": """# Neighborhood tree walk

- Capture ID: `CP-106`
- Status: reference
- Captured: 2026-08-10

## Source

> Map the labeled trees along the library walking loop.
> Start with the bur oak beside the west entrance.

## Connections

- [Window-box watering rhythm](../areas/window-box-watering-rhythm.md)
""",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def normalized_markdown(text: str) -> str:
    """Normalize presentation choices that the request did not constrain."""
    normalized: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith(("* ", "+ ")):
            line = "- " + line[2:]
        capture_prefix = "- Capture ID: `"
        if line.startswith(capture_prefix) and line.endswith("`"):
            line = "- Capture ID: " + line[len(capture_prefix) : -1]
        if normalized and normalized[-1] == "## Connections":
            if line.startswith("["):
                line = "- " + line
        normalized.append(line)
    return "\n".join(normalized)


def main() -> None:
    ignored_roots = {".git", ".sandbox-home", "vault"}
    actual_non_vault_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.relative_to(ROOT).parts[0] not in ignored_roots
    }
    if actual_non_vault_files != EXPECTED_NON_VAULT_FILES:
        missing = sorted(EXPECTED_NON_VAULT_FILES - actual_non_vault_files)
        extra = sorted(actual_non_vault_files - EXPECTED_NON_VAULT_FILES)
        fail(f"files outside vault changed; missing={missing}, extra={extra}")

    actual_non_vault_directories = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_dir()
        and path.relative_to(ROOT).parts[0] not in ignored_roots
    }
    if actual_non_vault_directories != EXPECTED_NON_VAULT_DIRECTORIES:
        missing = sorted(
            EXPECTED_NON_VAULT_DIRECTORIES - actual_non_vault_directories
        )
        extra = sorted(
            actual_non_vault_directories - EXPECTED_NON_VAULT_DIRECTORIES
        )
        fail(f"directories outside vault changed; missing={missing}, extra={extra}")

    if not INBOX.is_file():
        fail("inbox/captures.md is missing")
    digest = hashlib.sha256(INBOX.read_bytes()).hexdigest()
    if digest != INBOX_SHA256:
        fail("inbox/captures.md was modified")

    actual_files = {
        path.relative_to(VAULT).as_posix()
        for path in VAULT.rglob("*")
        if path.is_file()
    }
    expected_files = set(EXPECTED)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        fail(f"vault file set mismatch; missing={missing}, extra={extra}")

    for relative_path, expected_text in EXPECTED.items():
        path = VAULT / relative_path
        actual_text = path.read_text(encoding="utf-8")
        expected_normalized = normalized_markdown(expected_text)
        actual_normalized = normalized_markdown(actual_text)
        if actual_normalized != expected_normalized:
            diff = "".join(
                difflib.unified_diff(
                    expected_normalized.splitlines(keepends=True),
                    actual_normalized.splitlines(keepends=True),
                    fromfile=f"expected/{relative_path}",
                    tofile=f"actual/{relative_path}",
                )
            )
            fail(f"content mismatch in {relative_path}:\n{diff}")

    print("PASS: inbox preserved and knowledge vault matches all retained constraints")


if __name__ == "__main__":
    main()

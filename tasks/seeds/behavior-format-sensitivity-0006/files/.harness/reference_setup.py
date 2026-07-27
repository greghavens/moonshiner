#!/usr/bin/env python3
"""Exercise the reference payload through the genuine bridge executable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "tool_call.json"
AUDIT = ROOT / ".harness" / "runtime" / "commerce-audit.jsonl"
PAYLOAD_DIGEST = "4c9fda58024504c19295b3dc2033d0a425248ab64adaa297bc6d52cba07a25a7"


def main() -> None:
    if (
        not PAYLOAD.is_file()
        or hashlib.sha256(PAYLOAD.read_bytes()).hexdigest() != PAYLOAD_DIGEST
    ):
        return

    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.unlink(missing_ok=True)
    subprocess.run(
        ["./commerce-bridge", "invoke", "tool_call.json"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        start_new_session=True,
    )


if __name__ == "__main__":
    main()

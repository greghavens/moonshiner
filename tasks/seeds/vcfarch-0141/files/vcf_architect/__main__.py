"""Command-line entry point for the architecture generator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .planner import build_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a VCF migration architecture")
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--compatibility", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    compatibility = json.loads(args.compatibility.read_text(encoding="utf-8"))
    plan = build_plan(inventory, compatibility)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

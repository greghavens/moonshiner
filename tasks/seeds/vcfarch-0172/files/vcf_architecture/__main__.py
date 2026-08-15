"""Command-line entry point for the architecture generator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import build_architecture


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    architecture = build_architecture(args.inventory, args.snapshot)
    args.output.write_text(
        json.dumps(architecture, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for the architecture builder."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import build


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    build(Path.cwd(), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

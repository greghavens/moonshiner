"""Command-line entry point for the migration architecture generator."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="migration_plan.json")
    parser.parse_args()
    raise SystemExit("Implement the VCF migration architecture generator")


if __name__ == "__main__":
    raise SystemExit(main())

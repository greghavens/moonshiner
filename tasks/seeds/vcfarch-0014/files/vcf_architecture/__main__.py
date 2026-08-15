"""Command-line entry point for the architecture generator."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--estate", required=True)
    parser.add_argument("--compatibility", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.parse_args()
    raise NotImplementedError("architecture generator is not implemented")


if __name__ == "__main__":
    raise SystemExit(main())

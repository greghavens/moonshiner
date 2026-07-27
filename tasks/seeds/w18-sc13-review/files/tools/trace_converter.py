"""Developer-only adapter for converting captured lab traces."""

from __future__ import annotations

import argparse
import subprocess


def convert(converter: str, trace_path: str) -> None:
    command = f"{converter} --input {trace_path}"
    subprocess.run(command, check=True, shell=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("converter")
    parser.add_argument("trace_path")
    args = parser.parse_args()
    convert(args.converter, args.trace_path)

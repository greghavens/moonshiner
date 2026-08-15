"""Command-line entry point for the architecture generator."""

import argparse
import json
from pathlib import Path

from . import build_architecture


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--estate", required=True)
    parser.add_argument("--compatibility", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    sddc_spec, migration_plan = build_architecture(
        _read_json(args.requirements),
        _read_json(args.estate),
        _read_json(args.compatibility),
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("sddc-spec.json", sddc_spec),
        ("migration-plan.json", migration_plan),
    ):
        with (output_dir / name).open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")


if __name__ == "__main__":
    main()

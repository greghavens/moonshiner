"""Command line entry point for the batch deployment client.

    python -m vcfa_client --config CONFIG --batch BATCH --out RESULTS

Exits 0 when every deployment in the batch reached a terminal state, 1 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

from .client import run_batch

TERMINAL = ("SUCCESSFUL", "FAILED", "ABORTED", "APPROVAL_REJECTED")


def build_parser():
    parser = argparse.ArgumentParser(prog="vcfa_client")
    parser.add_argument("--config", required=True, help="Path to the appliance config JSON.")
    parser.add_argument("--batch", required=True, help="Path to the batch request JSON.")
    parser.add_argument("--out", required=True, help="Path to write the results JSON.")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    batch = json.loads(Path(args.batch).read_text())

    results = run_batch(config, batch)

    Path(args.out).write_text(json.dumps({"deployments": results}, indent=2) + "\n")

    if len(results) != len(batch["requests"]):
        return 1
    if any(item.get("status") not in TERMINAL for item in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

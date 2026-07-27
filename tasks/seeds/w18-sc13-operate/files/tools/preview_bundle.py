#!/usr/bin/env python3
"""Developer-only manifest preview tool; excluded from release packaging."""

import argparse
import json


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument(
        "--skip-signature",
        action="store_true",
        help="developer preview only",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)
    state = "skipped" if args.skip_signature else "required"
    print(json.dumps({"release": manifest.get("release"), "signature_check": state}))


if __name__ == "__main__":
    main()

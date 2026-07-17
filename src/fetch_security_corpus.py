#!/usr/bin/env python3
"""Hydrate the 18 pinned security-review repositories from their public remotes."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "security" / "catalog" / "repo_reviews.jsonl"
CLONES = ROOT / "security" / "corpus" / "clones"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated clone dirs")
    args = parser.parse_args(argv)
    if not CATALOG.exists():
        raise SystemExit(
            f"{CATALOG} is missing; run `moonshiner.py sec-import` first")
    wanted = set(args.only.split(",")) if args.only else None
    reviews = [json.loads(line) for line in CATALOG.read_text().splitlines() if line.strip()]
    for review in reviews:
        clone_dir = review["clone_dir"]
        if wanted and clone_dir not in wanted:
            continue
        destination = CLONES / clone_dir
        commit = review.get("commit")
        if destination.exists():
            probe = subprocess.run(
                ["git", "-C", str(destination), "cat-file", "-e", f"{commit}^{{commit}}"],
                capture_output=True,
            )
            if probe.returncode == 0:
                print(f"[skip] {clone_dir}: pinned commit already present")
                continue
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        remote = str(review["repo"])
        if not remote.startswith(("http://", "https://")):
            remote = f"https://github.com/{remote}.git"
        print(f"[clone] {clone_dir}: {remote}")
        subprocess.run(["git", "clone", "--filter=blob:none", remote, str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "checkout", "--detach", commit], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Author explicit Wave 10/11/14 catalog items directly into Moonshiner."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from common import ROOT, SEEDS_DIR

CHUNK_RE = re.compile(
    r"^#{2,3}\s+Chunk\s+(?P<id>[A-Z0-9]+)\b.*?×(?P<count>\d+)\b.*$",
    re.MULTILINE,
)
ITEM_RE = re.compile(r"^(?P<number>\d+)\.\s+(?P<text>.*)$", re.MULTILINE)
ID_RE = re.compile(r"\*\*([a-z][a-z0-9-]+)\*\*\s+[—-]\s+")
EXPECTED = {10: 300, 11: 90, 14: 300}


def catalog_items(path: Path) -> list[tuple[str, str, str]]:
    source = path.read_text()
    chunks = list(CHUNK_RE.finditer(source))
    result = []
    for position, chunk in enumerate(chunks):
        end = chunks[position + 1].start() if position + 1 < len(chunks) else len(source)
        body = source[chunk.end():end]
        items = list(ITEM_RE.finditer(body))
        for index, item in enumerate(items):
            item_end = items[index + 1].start() if index + 1 < len(items) else len(body)
            text = (item.group("text") + body[item.end():item_end]).strip()
            match = ID_RE.search(text)
            if not match:
                raise ValueError(f"{path.name} {chunk['id']} item {item['number']} has no seed ID")
            result.append((match.group(1), chunk["id"], text))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--waves", default="10,11,14")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    waves = [int(value) for value in args.waves.split(",") if value.strip()]
    if not args.yes:
        parser.error("metered wave authoring requires --yes")
    planned = []
    for wave in waves:
        if wave not in EXPECTED:
            parser.error(f"wave {wave} is not an explicit-item catalog")
        path = args.catalog_dir / f"WAVE{wave}_USECASES.md"
        items = catalog_items(path)
        if len(items) != EXPECTED[wave]:
            raise RuntimeError(f"Wave {wave}: expected {EXPECTED[wave]} items, found {len(items)}")
        planned.extend((wave, *item) for item in items)
    print(f"explicit wave author plan: {len(planned)} seeds across waves {waves}", flush=True)
    for position, (wave, seed_id, chunk, text) in enumerate(planned, 1):
        if (SEEDS_DIR / seed_id).exists():
            print(f"[{position}/{len(planned)}] existing {seed_id}", flush=True)
            continue
        brief = (f"Wave {wave}, chunk {chunk}. Author this exact adopted use case; do not "
                 f"broaden or omit its requirements. Include catalog provenance in task.json "
                 f"as {{\"wave\": {wave}, \"chunk\": \"{chunk}\"}}.\n\n{text}")
        print(f"[{position}/{len(planned)}] author {seed_id}", flush=True)
        result = subprocess.run([
            sys.executable, str(ROOT / "moonshiner.py"), "seed", "run",
            "--id", seed_id, "--brief", brief, "--max-attempts", "3", "--yes",
        ], cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

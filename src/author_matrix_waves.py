#!/usr/bin/env python3
"""Expand Wave 17/18 six-slot matrices into Moonshiner coding seeds."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from common import ROOT, SEEDS_DIR

RANGE_RE = re.compile(
    r"^###\s+(?P<wave>1[78])(?P<family>[A-Z]{2})(?P<first>\d{2})"
    r"[–-]1[78](?P=family)(?P<last>\d{2}).*?×(?P<seeds>\d+)\s*$",
    re.MULTILINE,
)
ITEM_RE = re.compile(r"^(?P<number>\d+)\.\s+(?P<text>.*)$", re.MULTILINE)
SLOTS = (
    ("design", "design and implement a complete bounded component or workflow"),
    ("integrate", "integrate or port a partial component while preserving its surrounding contracts"),
    ("diagnose", "reproduce, diagnose, and repair a realistic failure from supplied evidence"),
    ("operate", "apply the smallest fixture-authorized operational change and verify recovery"),
    ("review", "review the supplied implementation, find concrete flaws, and remediate them defensively"),
    ("exercise", "exercise a complete failure or interruption scenario and produce a verified handoff"),
)
EXPECTED = {17: (125, 750), 18: (125, 750)}


def matrix_items(path: Path, wave: int) -> list[tuple[str, str, str]]:
    source = path.read_text()
    ranges = [match for match in RANGE_RE.finditer(source)
              if int(match["wave"]) == wave]
    result = []
    for position, match in enumerate(ranges):
        end = ranges[position + 1].start() if position + 1 < len(ranges) else len(source)
        body = source[match.end():end]
        topics = list(ITEM_RE.finditer(body))
        count = int(match["last"]) - int(match["first"]) + 1
        if len(topics) < count:
            raise ValueError(f"{path.name} {match.group(0)} has {len(topics)} topics, expected {count}")
        for offset in range(count):
            topic = topics[offset]
            topic_end = topics[offset + 1].start() if offset + 1 < len(topics) else len(body)
            text = (topic["text"] + body[topic.end():topic_end]).strip()
            chunk = f"{wave}{match['family']}{int(match['first']) + offset:02d}"
            for slot_id, slot_text in SLOTS:
                result.append((f"w{wave}-{match['family'].lower()}"
                               f"{int(match['first']) + offset:02d}-{slot_id}", chunk,
                               f"{slot_text}. Curriculum focus: {text}"))
    chunks = {chunk for _, chunk, _ in result}
    expected_chunks, expected_seeds = EXPECTED[wave]
    if len(chunks) != expected_chunks or len(result) != expected_seeds:
        raise ValueError(f"Wave {wave} matrix expanded to {len(chunks)} chunks/"
                         f"{len(result)} seeds; expected {expected_chunks}/{expected_seeds}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--waves", default="17,18")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("metered wave authoring requires --yes")
    waves = [int(value) for value in args.waves.split(",") if value.strip()]
    planned = []
    for wave in waves:
        if wave not in EXPECTED:
            parser.error(f"unsupported matrix wave: {wave}")
        path = args.catalog_dir / f"WAVE{wave}_USECASES.md"
        planned.extend((wave, *item) for item in matrix_items(path, wave))
    print(f"matrix wave author plan: {len(planned)} seeds across waves {waves}", flush=True)
    for position, (wave, seed_id, chunk, objective) in enumerate(planned, 1):
        if (SEEDS_DIR / seed_id).exists():
            print(f"[{position}/{len(planned)}] existing {seed_id}", flush=True)
            continue
        domain = "site reliability engineering" if wave == 17 else "firmware engineering"
        brief = f"""Wave {wave}, chunk {chunk}, supervised {domain} curriculum.
Author this exact objective as a deterministic, workspace-local coding-repair
seed: {objective}

Use only simulated services/devices and fake non-secret fixtures. No public
network, live infrastructure, real credentials, host-global mutation, sleeps
as correctness, or destructive host actions. The prompt must establish scope,
authority, evidence, reversible action boundaries, and observable success.
Protected tests must prove every stated requirement, adjacent invariants,
failure behavior, and cleanup or rollback. Include catalog provenance in
task.json as {{"wave": {wave}, "chunk": "{chunk}"}}. Do not broaden the task."""
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

#!/usr/bin/env python3
"""Derive cumulative next-assistant-action rows from whole trajectories.

``data/full`` stays the canonical, tracked source representation. A source
trajectory with N assistant messages produces N derived examples: example k is
the exact message prefix through assistant action k inclusive, so every earlier
assistant action and tool result is context and the final assistant message is
the sole next-step target. The derivation is additive and atomic — it never
rewrites its inputs and is independent of which runtime produced the trace.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path

from common import DATA

DEFAULT_INPUT = DATA / "full"
DEFAULT_OUTPUT = DATA / "next_step"
SPLITS = ("train", "val")
DERIVATION = "cumulative-next-assistant-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprint(record: dict) -> str:
    """Stable identity for one source row."""
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def expand_record(record: dict) -> list[dict]:
    """Return one exact cumulative prefix per assistant message."""
    messages = record.get("messages") or []
    positions = [index for index, message in enumerate(messages)
                 if message.get("role") == "assistant"]
    if not positions:
        raise ValueError("trajectory has no assistant messages")

    fingerprint = source_fingerprint(record)
    task = (record.get("meta") or {}).get("task", "unknown")
    source_id = f"{task}:{fingerprint[:20]}"
    base_meta = dict(record.get("meta") or {})
    expanded = []
    for step, target_index in enumerate(positions, 1):
        meta = {
            **base_meta,
            "derivation": DERIVATION,
            "source_trajectory_id": source_id,
            "source_sha256": fingerprint,
            "assistant_step": step,
            "assistant_steps": len(positions),
            "target_message_index": target_index,
            "original_n_messages": len(messages),
        }
        expanded.append({
            "messages": copy.deepcopy(messages[:target_index + 1]),
            "meta": meta,
        })
    return expanded


def write_split(source: Path, destination: Path) -> dict:
    trajectories = examples = 0
    steps_per_trajectory = collections.Counter()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with source.open() as input_handle, temporary.open("w") as output_handle:
        for line_number, line in enumerate(input_handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            derived = expand_record(record)
            trajectories += 1
            examples += len(derived)
            steps_per_trajectory[len(derived)] += 1
            for row in derived:
                if row["messages"][-1].get("role") != "assistant":
                    raise AssertionError(
                        f"{source}:{line_number}: derived target is not assistant")
                output_handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(destination)
    return {
        "trajectories": trajectories,
        "examples": examples,
        "assistant_steps_per_trajectory": {
            str(key): value for key, value in sorted(steps_per_trajectory.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    for split in SPLITS:
        source = args.input / f"{split}.jsonl"
        if not source.exists():
            parser.error(f"missing input split: {source}")

    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "derivation": DERIVATION,
        "input_dir": str(args.input.resolve()),
        "output_dir": str(args.output.resolve()),
        "inputs": {},
        "outputs": {},
        "splits": {},
    }
    for split in SPLITS:
        source = args.input / f"{split}.jsonl"
        destination = args.output / f"{split}.jsonl"
        manifest["inputs"][source.name] = sha256(source)
        manifest["splits"][split] = write_split(source, destination)
        manifest["outputs"][destination.name] = sha256(destination)

    manifest_path = args.output / "MANIFEST.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(manifest_path)

    total_sources = sum(value["trajectories"]
                        for value in manifest["splits"].values())
    total_examples = sum(value["examples"]
                         for value in manifest["splits"].values())
    print(f"wrote {args.output}: {total_examples} cumulative next-step rows "
          f"from {total_sources} untouched source trajectories")


if __name__ == "__main__":
    main()

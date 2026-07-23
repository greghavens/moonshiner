#!/usr/bin/env python3
"""Export cumulative next-step rows to the Hugging Face staging file.

The tracked whole-session file ``data/hf/traces.jsonl`` is not modified. Public
Hugging Face publication uploads ``data/hf-publish/traces.jsonl`` as the remote
canonical file, produced here after validating source freshness (via the
next-step manifest), placement, cumulative prefixes, and trajectory-disjoint
splits. Attestation columns ride along so the export can be gated on provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common import DATA, RUNS
from expand_next_steps import DERIVATION
from hf_sync import ensure_local_dataset
from trace_provenance import value as provenance

DEFAULT_INPUT = DATA / "next_step"
DEFAULT_SOURCE = DATA / "full"
DEFAULT_OUTPUT = DATA / "hf-publish" / "traces.jsonl"
SPLITS = ("train", "val")

# The exact key order build_row() emits; the validator gates on this.
PUBLISH_KEY_ORDER = [
    "task", "source_trajectory_id", "source_trajectory_sha256", "lang",
    "category", "domain", "verifier", "split", "teacher_runtime", "teacher_model",
    "reasoning_effort", "provider", "observed_models", "model_attested",
    "trace_format", "tools_used", "derivation", "assistant_step",
    "assistant_steps", "target_message_index", "original_n_messages",
    "n_messages", "messages", "tools"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_manifest(input_dir: Path, source_dir: Path) -> None:
    path = input_dir / "MANIFEST.json"
    if not path.exists():
        raise ValueError(f"next-step manifest is missing: {path}")
    manifest = json.loads(path.read_text())
    if manifest.get("derivation") != DERIVATION:
        raise ValueError("unexpected or missing next-step derivation")
    for split in SPLITS:
        source = source_dir / f"{split}.jsonl"
        expected = (manifest.get("inputs") or {}).get(source.name)
        if not source.exists() or not expected or sha256(source) != expected:
            raise ValueError(f"next-step input is stale for {split}")
        derived = input_dir / f"{split}.jsonl"
        derived_expected = (manifest.get("outputs") or {}).get(derived.name)
        if not derived.exists() or not derived_expected \
                or sha256(derived) != derived_expected:
            raise ValueError(f"next-step output is stale for {split}")


def build_row(record: dict, split: str) -> dict:
    meta = record["meta"]
    return {
        "task": meta["task"],
        "source_trajectory_id": meta["source_trajectory_id"],
        "source_trajectory_sha256": meta["source_sha256"],
        "lang": meta.get("lang"),
        "category": meta.get("category"),
        "domain": meta.get("domain", "coding"),
        "verifier": meta.get("verifier", "acceptance-tests+quality-review"),
        "split": split,
        "teacher_runtime": provenance(record, "teacher_runtime"),
        "teacher_model": provenance(record, "teacher_model"),
        "reasoning_effort": provenance(record, "reasoning_effort"),
        "provider": provenance(record, "provider"),
        "observed_models": provenance(record, "observed_models", []),
        "model_attested": bool(provenance(record, "model_attested", False)),
        "trace_format": provenance(record, "trace_format"),
        "tools_used": meta.get("tools_used", []),
        "derivation": meta["derivation"],
        "assistant_step": meta["assistant_step"],
        "assistant_steps": meta["assistant_steps"],
        "target_message_index": meta["target_message_index"],
        "original_n_messages": meta["original_n_messages"],
        "n_messages": len(record["messages"]),
        "messages": record["messages"],
        "tools": json.dumps(record.get("tools") or []),
    }


def validate_export(path: Path) -> dict:
    """Prove every source is a complete sequence of exact cumulative prefixes."""
    groups: dict = {}
    split_by_source: dict = {}
    with path.open() as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            messages = row.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError(f"line {number}: messages must be non-empty")
            if messages[-1].get("role") != "assistant":
                raise ValueError(f"line {number}: target is not assistant")
            if row.get("target_message_index") != len(messages) - 1:
                raise ValueError(f"line {number}: target is not final")
            step, total = row.get("assistant_step"), row.get("assistant_steps")
            if (not isinstance(step, int) or not isinstance(total, int)
                    or not 1 <= step <= total):
                raise ValueError(f"line {number}: invalid step metadata")
            if sum(message.get("role") == "assistant"
                   for message in messages) != step:
                raise ValueError(f"line {number}: assistant context count mismatch")
            if not isinstance(json.loads(row.get("tools", "null")), list):
                raise ValueError(f"line {number}: tools must encode a list")
            source_id = row.get("source_trajectory_id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError(f"line {number}: source trajectory id is missing")
            prior_split = split_by_source.setdefault(source_id, row.get("split"))
            if prior_split != row.get("split"):
                raise ValueError(f"source {source_id} crosses train/val")
            groups.setdefault(source_id, []).append(
                (step, total, messages, number, row.get("task")))

    for source_id, entries in groups.items():
        entries.sort()
        totals = {entry[1] for entry in entries}
        if len(totals) != 1:
            raise ValueError(f"{source_id}: inconsistent assistant_steps")
        total = totals.pop()
        if [entry[0] for entry in entries] != list(range(1, total + 1)):
            raise ValueError(f"{source_id}: incomplete or duplicate steps")
        if len({entry[4] for entry in entries}) != 1:
            raise ValueError(f"{source_id}: inconsistent task identity")
        for previous, current in zip(entries, entries[1:]):
            prior_messages, current_messages = previous[2], current[2]
            if current_messages[:len(prior_messages)] != prior_messages:
                raise ValueError(
                    f"{source_id} step {current[0]} is not an exact cumulative prefix")
    return {"rows": sum(len(entries) for entries in groups.values()),
            "trajectories": len(groups)}


def row_identity(row: dict) -> tuple[str, int]:
    source_id, step = row.get("source_trajectory_id"), row.get("assistant_step")
    if not isinstance(source_id, str) or not source_id or not isinstance(step, int):
        raise ValueError("published row lacks source trajectory/assistant step identity")
    return source_id, step


def upsert_journal(output: Path, journal: Path) -> tuple[int, int]:
    """Atomically replace rows by their central task identity.

    A task has one accepted trajectory. New task IDs append naturally; a new
    accepted attempt for an existing task replaces only that task's rows. No
    content hash, category rule, or alternate publication path is involved.
    """
    journal_lines = [line for line in journal.read_text().splitlines() if line.strip()]
    replacement_tasks = {json.loads(line).get("task") for line in journal_lines}
    if None in replacement_tasks:
        raise ValueError("journal row is missing task identity")
    retained: list[str] = []
    replaced_rows = 0
    if output.exists():
        with output.open() as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("task") in replacement_tasks:
                    replaced_rows += 1
                else:
                    # Early canonical next-step rows predate the explicit
                    # source ID. Moonshiner has always kept one accepted
                    # trajectory per central task identity, so the task ID is
                    # the lossless source identity for those retained rows.
                    if not row.get("source_trajectory_id") and row.get("task"):
                        row["source_trajectory_id"] = row["task"]
                    retained.append(json.dumps(row, ensure_ascii=False))
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(output.suffix + ".upsert.pending")
    with pending.open("w") as destination:
        for line in [*retained, *journal_lines]:
            destination.write(line + "\n")
        destination.flush()
        os.fsync(destination.fileno())
    pending.replace(output)
    return len(journal_lines), replaced_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--task", action="append",
                        help="Append only this accepted trajectory (repeatable)")
    args = parser.parse_args()

    validate_manifest(args.input, args.source)
    ensure_local_dataset(target=args.output)
    journal_dir = RUNS / "export-journals"
    journal_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    journal = journal_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
    counts = {}
    selected_tasks: set[str] = set()
    with journal.open("x") as output_handle:
        for split in SPLITS:
            source = args.input / f"{split}.jsonl"
            counts[split] = 0
            with source.open() as input_handle:
                for line in input_handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if args.task and (record.get("meta") or {}).get("task") not in set(args.task):
                        continue
                    selected_tasks.add((record.get("meta") or {}).get("task"))
                    output_handle.write(json.dumps(
                        build_row(record, split),
                        ensure_ascii=False) + "\n")
                    counts[split] += 1
    if args.task:
        missing = set(args.task) - selected_tasks
        if missing:
            raise ValueError(f"accepted trajectories are not buildable: {sorted(missing)}")
    validate_export(journal)
    written, replaced = upsert_journal(args.output, journal)
    validation = validate_export(args.output)
    print(f"task-keyed export {args.output}: written={written} replaced={replaced}; "
          f"candidate rows={sum(counts.values())} "
          f"({', '.join(f'{key}={value}' for key, value in counts.items())}); "
          f"validated {validation['trajectories']} cumulative trajectories")


if __name__ == "__main__":
    main()

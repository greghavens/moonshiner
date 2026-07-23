#!/usr/bin/env python3
"""Fold whole-session sources into one tracked repository JSONL for provenance.

This writes ``data/full/{train,val}.jsonl`` into ``data/hf/traces.jsonl`` so the
immutable whole-trajectory sources are captured in Git. Hugging Face publication
does not upload this file; the public canonical file is the cumulative next-step
export produced by ``export_hf_next_steps.py``. Every row records the teacher
runtime/model and its stream attestation so ``validate_hf_export.py`` can gate on
provenance regardless of which runtime produced the trace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from common import DATA
from canonical_dataset import canonical_category, normalize_messages
from trace_provenance import value as provenance

FULL = DATA / "full"
OUT = DATA / "hf" / "traces.jsonl"
SPLITS = ("train", "val")
KEY_ORDER = ["task", "lang", "category", "domain", "verifier", "split",
             "teacher_runtime", "teacher_model", "reasoning_effort", "provider",
             "observed_models", "model_attested", "trace_format", "tools_used",
             "n_messages", "messages"]


def build_row(record: dict, split: str) -> dict:
    """One HF row from a full-trace record, in canonical key order.

    ``messages`` stays native JSON; ``tools`` is serialized to a JSON string.
    """
    meta = record["meta"]
    return {
        "task": meta["task"],
        "lang": meta.get("lang"),
        "category": canonical_category(meta["task"], meta.get("category")),
        "domain": meta.get("domain", "coding"),
        "verifier": meta.get("verifier", "acceptance-tests+protected-file-hash"),
        "split": split,
        "teacher_runtime": provenance(record, "teacher_runtime"),
        "teacher_model": provenance(record, "teacher_model"),
        "reasoning_effort": provenance(record, "reasoning_effort"),
        "provider": provenance(record, "provider"),
        "observed_models": provenance(record, "observed_models", []),
        "model_attested": bool(provenance(record, "model_attested", False)),
        "trace_format": provenance(record, "trace_format"),
        "tools_used": meta.get("tools_used", []),
        "n_messages": len(record["messages"]),
        "messages": normalize_messages(record["messages"]),
    }


def main() -> None:
    for split in SPLITS:
        source = FULL / f"{split}.jsonl"
        if not source.exists():
            sys.exit(f"missing input file: {source} — cannot export")

    counts = {split: 0 for split in SPLITS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as handle:
        for split in SPLITS:
            with (FULL / f"{split}.jsonl").open() as source_handle:
                for line in source_handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    handle.write(json.dumps(build_row(record, split)) + "\n")
                    counts[split] += 1

    seen = {split: 0 for split in SPLITS}
    with OUT.open() as handle:
        for number, line in enumerate(handle, 1):
            row = json.loads(line)
            if list(row.keys()) != KEY_ORDER:
                raise AssertionError(
                    f"line {number}: key order {list(row.keys())} != {KEY_ORDER}")
            seen[row["split"]] += 1
    if seen != counts:
        raise AssertionError(f"re-read counts {seen} != written {counts}")

    total = sum(counts.values())
    print(f"wrote {OUT}: {total} rows "
          f"({', '.join(f'{split}={counts[split]}' for split in SPLITS)})")


if __name__ == "__main__":
    main()

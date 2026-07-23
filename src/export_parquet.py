#!/usr/bin/env python3
"""Export cumulative next-step rows to optional Parquet files.

Column design: ``messages`` is JSON-encoded rather than a nested Parquet struct
because tool-call arguments vary per row. Consumers do
``json.loads(row["messages"])``. Flat metadata (including teacher provenance)
rides as real columns. Rows pass through verbatim from ``data/next_step`` after
source verification and cumulative expansion; this adds nothing and drops nothing.

  python3 src/export_parquet.py       # -> data/hf-publish/{train,val}.parquet
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from common import DATA

STRING_COLUMNS = ("task", "source_trajectory_id", "source_sha256", "lang",
                  "category", "domain", "teacher_runtime", "teacher_model",
                  "reasoning_effort", "provider", "trace_format")
INT_COLUMNS = ("assistant_step", "assistant_steps", "target_message_index",
               "original_n_messages", "n_messages")


def export(src: Path, dst: Path) -> int:
    columns: dict = {name: [] for name in STRING_COLUMNS}
    columns.update({name: [] for name in INT_COLUMNS})
    columns.update({"tools_used": [], "observed_models": [],
                    "model_attested": [], "messages": []})
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        meta = row.get("meta", {})
        for name in STRING_COLUMNS:
            columns[name].append(meta.get(name))
        columns["assistant_step"].append(meta.get("assistant_step"))
        columns["assistant_steps"].append(meta.get("assistant_steps"))
        columns["target_message_index"].append(meta.get("target_message_index"))
        columns["original_n_messages"].append(meta.get("original_n_messages"))
        columns["n_messages"].append(len(row["messages"]))
        columns["tools_used"].append(meta.get("tools_used", []))
        columns["observed_models"].append(meta.get("observed_models", []))
        columns["model_attested"].append(bool(meta.get("model_attested")))
        columns["messages"].append(json.dumps(row["messages"], ensure_ascii=False))

    table = pa.table({
        **{name: pa.array(columns[name], pa.string()) for name in STRING_COLUMNS},
        **{name: pa.array(columns[name], pa.int32()) for name in INT_COLUMNS},
        "tools_used": pa.array(columns["tools_used"], pa.list_(pa.string())),
        "observed_models": pa.array(columns["observed_models"], pa.list_(pa.string())),
        "model_attested": pa.array(columns["model_attested"], pa.bool_()),
        "messages": pa.array(columns["messages"], pa.string()),
    })
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dst, compression="zstd")
    return table.num_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DATA / "next_step"))
    parser.add_argument("--out", default=str(DATA / "hf-publish"))
    args = parser.parse_args()

    for split in ("train", "val"):
        src = Path(args.data) / f"{split}.jsonl"
        if not src.exists():
            continue
        dst = Path(args.out) / f"{split}.parquet"
        rows = export(src, dst)
        print(f"{split}: {rows} rows -> {dst} ({dst.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

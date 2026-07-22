#!/usr/bin/env python3
"""One-time conversion of an existing whole-session JSONL to Moonshiner's canonical rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from common import CONFIG, DATA
from expand_next_steps import expand_record
from export_hf_next_steps import PUBLISH_KEY_ORDER, build_row, validate_export


def _tool_schemas() -> dict[tuple[str, str], list[dict]]:
    schemas = {}
    for split in ("train", "val"):
        path = DATA / "next_step" / f"{split}.jsonl"
        if not path.is_file():
            continue
        with path.open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                meta = record.get("meta") or {}
                tools = record.get("tools") or []
                if tools:
                    schemas[(str(meta.get("teacher_runtime") or ""),
                             str(meta.get("trace_format") or ""))] = tools
    return schemas


def migrate(path: Path) -> tuple[int, int]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("dataset is empty")
    if all(list(row) == PUBLISH_KEY_ORDER for row in rows):
        validation = validate_export(path)
        return validation["trajectories"], validation["rows"]
    if any("source_trajectory_id" in row or "assistant_step" in row for row in rows):
        raise ValueError("dataset mixes canonical and non-canonical rows")

    schemas = _tool_schemas()
    val_fraction = float((CONFIG.get("build") or {}).get("val_frac", 0.08))
    converted = []
    for row in rows:
        runtime = str(row.get("teacher_runtime") or "")
        trace_format = str(row.get("trace_format") or "")
        tools = schemas.get((runtime, trace_format))
        if not tools:
            raise ValueError(
                f"{row.get('task')}: no genuine tool schema is available for "
                f"{runtime}/{trace_format}")
        messages = row.get("messages") or []
        used = sorted({call.get("function", {}).get("name")
                       for message in messages
                       for call in (message.get("tool_calls") or [])
                       if call.get("function", {}).get("name")})
        record = {"messages": messages, "tools": tools, "meta": {
            "task": row["task"], "lang": row.get("lang") or "en",
            "category": row.get("category") or "Other verified work",
            "domain": row.get("domain") or "coding",
            "verifier": row.get("verifier") or "published-baseline",
            "teacher_runtime": runtime, "teacher_model": row.get("teacher_model"),
            "reasoning_effort": row.get("reasoning_effort"),
            "provider": row.get("provider"),
            "observed_models": row.get("observed_models") or [row.get("teacher_model")],
            "model_attested": row.get("model_attested") is True,
            "trace_format": trace_format, "tools_used": used}}
        split = ("val" if int(hashlib.sha1(row["task"].encode()).hexdigest(), 16) % 100
                 < int(val_fraction * 100) else "train")
        converted.extend(build_row(item, split) for item in expand_record(record))

    backup = path.with_name(path.name + ".pre-canonical")
    if not backup.exists():
        shutil.copy2(path, backup)
    pending = path.with_suffix(path.suffix + ".canonical.pending")
    with pending.open("x") as handle:
        for row in converted:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    validation = validate_export(pending)
    pending.replace(path)
    return validation["trajectories"], validation["rows"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner migrate-dataset")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("migration requires --yes")
    path = DATA / "hf-publish" / "traces.jsonl"
    trajectories, rows = migrate(path)
    print(f"canonical dataset ready: {trajectories} trajectories, {rows} training rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

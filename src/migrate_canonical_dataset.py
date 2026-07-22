#!/usr/bin/env python3
"""One-time conversion of an existing whole-session JSONL to Moonshiner's canonical rows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from common import CONFIG, DATA
from expand_next_steps import expand_record
from export_hf_next_steps import PUBLISH_KEY_ORDER, build_row, validate_export
from hf_sync import sha256


def _tool_schemas() -> dict[str, list[dict]]:
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
                    schemas[str(meta.get("trace_format") or "")] = tools
    return schemas


def _advance_baseline(path: Path, validation: dict) -> None:
    publish = CONFIG.get("publish") or {}
    dataset = publish.get("hf_dataset")
    filename = str(publish.get("filename") or path.name)
    if not dataset:
        return
    marker_name = hashlib.sha256(f"{dataset}:{filename}".encode()).hexdigest()[:16]
    marker = DATA / "hf-sync" / f"{marker_name}.json"
    if not marker.is_file():
        # A freshly imported dataset has not been bootstrapped by the publisher
        # yet.  The first publish records this already-local canonical file as
        # its baseline before uploading it.
        return
    state = json.loads(marker.read_text())
    state.update({"bootstrap_sha256": sha256(path),
                  "bootstrap_size": path.stat().st_size,
                  "bootstrap_rows": validation["rows"],
                  "canonical_migrated_at": datetime.now(timezone.utc).isoformat(
                      timespec="seconds")})
    pending_marker = marker.with_suffix(".pending")
    pending_marker.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    pending_marker.replace(marker)


def migration_path() -> Path:
    """Return the one canonical migration target, seeding it from one import."""
    target = DATA / "hf-publish" / "traces.jsonl"
    if target.is_file():
        return target
    imported = sorted((DATA / "imported").glob("*/rows.jsonl"))
    if not imported:
        return target
    if len(imported) != 1:
        raise ValueError(
            "multiple imported datasets exist; migrate each in a separate project")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(imported[0], target)
    return target


def _historical_canonical(rows: list[dict]) -> list[dict] | None:
    """Upgrade the former canonical columns without altering trace content."""
    required = {
        "task", "source_trajectory_id", "source_trajectory_sha256", "split",
        "derivation", "assistant_step", "assistant_steps",
        "target_message_index", "original_n_messages", "messages", "tools",
    }
    if not all(required <= set(row) for row in rows):
        return None
    converted = []
    for row in rows:
        tools = row.get("tools")
        if not isinstance(tools, str):
            tools = json.dumps(tools or [], ensure_ascii=False)
        model = row.get("teacher_model")
        converted.append({
            "task": row["task"],
            "source_trajectory_id": row["source_trajectory_id"],
            "source_trajectory_sha256": row["source_trajectory_sha256"],
            "lang": row.get("lang"),
            "category": row.get("category"),
            "domain": row.get("domain", "coding"),
            "verifier": row.get("verifier", "acceptance-tests+quality-review"),
            "split": row["split"],
            "teacher_runtime": row.get("teacher_runtime"),
            "teacher_model": model,
            "reasoning_effort": row.get("reasoning_effort"),
            "provider": row.get("provider"),
            "observed_models": row.get("observed_models") or ([model] if model else []),
            "model_attested": bool(row.get("model_attested")),
            "trace_format": row.get("trace_format"),
            "tools_used": row.get("tools_used") or [],
            "derivation": row["derivation"],
            "assistant_step": row["assistant_step"],
            "assistant_steps": row["assistant_steps"],
            "target_message_index": row["target_message_index"],
            "original_n_messages": row["original_n_messages"],
            "n_messages": len(row["messages"]),
            "messages": row["messages"],
            "tools": tools,
        })
    return converted


def migrate(path: Path) -> tuple[int, int]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("dataset is empty")
    if all(list(row) == PUBLISH_KEY_ORDER for row in rows):
        validation = validate_export(path)
        _advance_baseline(path, validation)
        return validation["trajectories"], validation["rows"]
    historical = _historical_canonical(rows)
    if historical is not None:
        backup = path.with_name(path.name + ".pre-canonical")
        if not backup.exists():
            shutil.copy2(path, backup)
        pending = path.with_suffix(path.suffix + ".canonical.pending")
        with pending.open("x") as handle:
            for row in historical:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush(); os.fsync(handle.fileno())
        validation = validate_export(pending)
        pending.replace(path)
        _advance_baseline(path, validation)
        return validation["trajectories"], validation["rows"]
    if any("source_trajectory_id" in row or "assistant_step" in row for row in rows):
        raise ValueError("dataset mixes canonical and non-canonical rows")

    schemas = _tool_schemas()
    val_fraction = float((CONFIG.get("build") or {}).get("val_frac", 0.08))
    converted = []
    for row in rows:
        runtime = str(row.get("teacher_runtime") or "")
        trace_format = str(row.get("trace_format") or "")
        tools = schemas.get(trace_format)
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
    _advance_baseline(path, validation)
    return validation["trajectories"], validation["rows"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner migrate-dataset")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("migration requires --yes")
    path = migration_path()
    trajectories, rows = migrate(path)
    print(f"canonical dataset ready: {trajectories} trajectories, {rows} training rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

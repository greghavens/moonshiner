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
from canonical_dataset import (INTERNAL_CONTENT_MARKERS, PUBLISH_KEY_ORDER,
                               normalize_public_row)
from expand_next_steps import expand_record
from export_hf_next_steps import build_row, validate_export
from hf_sync import sha256
from privacy import EMAIL_RE, sanitize_object


def _privacy_scrub_row(row: dict) -> dict:
    scrubbed = sanitize_object(row)
    serialized = json.dumps(scrubbed, ensure_ascii=False)
    return json.loads(EMAIL_RE.sub("[REDACTED_EMAIL]", serialized))


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


def _historical_canonical(
        rows: list[dict], source_hashes: dict[str, str] | None = None
        ) -> list[dict] | None:
    """Upgrade the former canonical columns without altering trace content."""
    required = {
        "task", "source_trajectory_id", "split",
        "derivation", "assistant_step", "assistant_steps",
        "target_message_index", "original_n_messages", "messages", "tools",
    }
    if not all(required <= set(row) for row in rows):
        return None
    teacher = CONFIG.get("teacher") or {}
    configured_runtime = str(teacher.get("runtime") or "historical")
    runtime_config = (CONFIG.get("runtimes") or {}).get(configured_runtime) or {}
    final_by_source = {}
    for row in rows:
        source = row["source_trajectory_id"]
        prior = final_by_source.get(source)
        if prior is None or row["assistant_step"] > prior["assistant_step"]:
            final_by_source[source] = row
    source_hashes = source_hashes or {
        source: _source_hash(final) for source, final in final_by_source.items()}
    converted = []
    for row in rows:
        runtime = row.get("teacher_runtime") or configured_runtime
        model = row.get("teacher_model") or teacher.get("model")
        provider = row.get("provider") or (
            ((CONFIG.get("runtimes") or {}).get(str(runtime)) or {}).get("provider")
            or runtime_config.get("provider") or "historical")
        attested = bool(row.get("model_attested"))
        converted.append(normalize_public_row({
            "task": row["task"],
            "source_trajectory_id": row["source_trajectory_id"],
            "source_trajectory_sha256": (
                row.get("source_trajectory_sha256")
                or source_hashes[row["source_trajectory_id"]]),
            "lang": row.get("lang"),
            "category": row.get("category"),
            "domain": row.get("domain", "coding"),
            "verifier": (row.get("verifier", "acceptance-tests+quality-review")
                         if attested else "published-baseline"),
            "split": row["split"],
            "teacher_runtime": runtime,
            "teacher_model": model,
            "reasoning_effort": row.get("reasoning_effort") or teacher.get("reasoning"),
            "provider": provider,
            "observed_models": row.get("observed_models") or ([model] if model else []),
            "model_attested": attested,
            "trace_format": row.get("trace_format"),
            "tools_used": row.get("tools_used") or [],
            "derivation": row["derivation"],
            "assistant_step": row["assistant_step"],
            "assistant_steps": row["assistant_steps"],
            "target_message_index": row["target_message_index"],
            "original_n_messages": row["original_n_messages"],
            "n_messages": len(row["messages"]),
            "messages": row["messages"],
        }))
    return converted


def _legacy_public(
        rows: list[dict], source_hashes: dict[str, str] | None = None,
        source_lengths: dict[str, int] | None = None,
        ) -> list[dict] | None:
    """Upgrade the original ten-column public rows without inventing trace data."""
    required = {
        "task", "lang", "category", "split", "assistant_step",
        "assistant_steps", "target_message_index", "n_messages", "messages", "tools",
    }
    if not all(required == set(row) for row in rows):
        return None
    teacher = CONFIG.get("teacher") or {}
    runtime = str(teacher.get("runtime") or "historical")
    runtime_config = (CONFIG.get("runtimes") or {}).get(runtime) or {}
    model = teacher.get("model")
    provider = runtime_config.get("provider") or "historical"
    final_by_task = {}
    for row in rows:
        prior = final_by_task.get(row["task"])
        if prior is None or row["assistant_step"] > prior["assistant_step"]:
            final_by_task[row["task"]] = row
    source_hashes = source_hashes or {
        task: _source_hash(final) for task, final in final_by_task.items()}
    source_lengths = source_lengths or {
        task: len(final["messages"]) for task, final in final_by_task.items()}
    converted = []
    for row in rows:
        calls = [call for message in row["messages"]
                 for call in (message.get("tool_calls") or [])]
        converted.append(normalize_public_row({
            "task": row["task"],
            "source_trajectory_id": row["task"],
            "source_trajectory_sha256": source_hashes[row["task"]],
            "lang": row.get("lang"),
            "category": row.get("category"),
            "domain": "agent",
            "verifier": "published-baseline",
            "split": row["split"],
            "teacher_runtime": runtime,
            "teacher_model": model,
            "reasoning_effort": teacher.get("reasoning"),
            "provider": provider,
            "observed_models": [model] if model else [],
            "model_attested": False,
            "trace_format": "historical-canonical",
            "tools_used": sorted({
                str((call.get("function") or {}).get("name"))
                for call in calls if (call.get("function") or {}).get("name")}),
            "derivation": "cumulative-next-assistant-v1",
            "assistant_step": row["assistant_step"],
            "assistant_steps": row["assistant_steps"],
            "target_message_index": row["target_message_index"],
            "original_n_messages": source_lengths[row["task"]],
            "n_messages": len(row["messages"]),
            "messages": row["messages"],
        }))
    return converted


def _legacy_enriched(
        row: dict, source_length: int,
        ) -> dict:
    """Upgrade the pre-source-identity enriched public row."""
    values = dict(row)
    values.update({
        "source_trajectory_id": row["task"],
        "teacher_runtime": row.get("teacher_runtime") or row.get("runtime"),
        "model_attested": bool(row.get("model_attested")),
        "original_n_messages": source_length,
    })
    return normalize_public_row(values)


def _current_canonical(row: dict, source_hash: str | None = None) -> dict:
    """Fill missing project provenance on an otherwise canonical row."""
    teacher = CONFIG.get("teacher") or {}
    runtime = row.get("teacher_runtime") or teacher.get("runtime")
    runtime_config = ((CONFIG.get("runtimes") or {}).get(str(runtime)) or {})
    model = row.get("teacher_model") or teacher.get("model")
    values = dict(row)
    values.update({
        "source_trajectory_sha256": (
            source_hash or row.get("source_trajectory_sha256")),
        "teacher_runtime": runtime,
        "teacher_model": model,
        "reasoning_effort": (
            row.get("reasoning_effort") or teacher.get("reasoning")),
        "provider": (
            row.get("provider") or runtime_config.get("provider") or runtime),
        "observed_models": (
            row.get("observed_models") or ([model] if model else [])),
        # Missing provenance may be restored from explicit project
        # configuration, but attestation is never inferred.
        "model_attested": bool(row.get("model_attested")),
    })
    return normalize_public_row(values)


def _source_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(
        {"messages": row["messages"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _contains_internal_control(row: dict) -> bool:
    return any(
        marker in str(message.get("content") or "")
        for message in (row.get("messages") or [])
        for marker in INTERNAL_CONTENT_MARKERS)


def _generation(row: dict) -> str | None:
    public_keys = {
        "task", "lang", "category", "split", "assistant_step",
        "assistant_steps", "target_message_index", "n_messages", "messages", "tools",
    }
    historical_required = {
        "task", "source_trajectory_id", "split", "derivation",
        "assistant_step", "assistant_steps", "target_message_index",
        "original_n_messages", "messages", "tools",
    }
    enriched_required = {
        "task", "source_trajectory_sha256", "split", "derivation",
        "assistant_step", "assistant_steps", "target_message_index",
        "messages", "tools",
    }
    if set(row) == public_keys:
        return "public"
    if list(row) == PUBLISH_KEY_ORDER:
        return "current"
    if historical_required <= set(row):
        return "historical"
    if enriched_required <= set(row):
        return "enriched"
    # Published rows from an earlier Moonshiner release may already carry a
    # source trajectory identity while lacking one or more columns introduced
    # by a later schema revision.  They are canonical rows to normalize, not
    # whole-session source records.  The shared normalizer supplies the current
    # shape and the validator remains the authority on whether the result is
    # publishable.
    if {"task", "source_trajectory_id", "messages", "tools"} <= set(row):
        return "current"
    return None


def _recognized_canonical(rows: list[dict]) -> list[dict] | None:
    """Normalize a file containing any mixture of known published generations."""
    public, current, historical, enriched = [], [], [], []
    for row in rows:
        generation = _generation(row)
        if generation == "public":
            public.append(row)
        elif generation == "current":
            current.append(row)
        elif generation == "historical":
            historical.append(row)
        elif generation == "enriched":
            enriched.append(row)
    if len(public) + len(current) + len(historical) + len(enriched) != len(rows):
        return None
    public_converted = iter(_legacy_public(public) or [])
    historical_converted = iter(_historical_canonical(historical) or [])
    public_ids = {id(row) for row in public}
    current_ids = {id(row) for row in current}
    enriched_ids = {id(row) for row in enriched}
    enriched_lengths = {
        row["task"]: max(
            len(candidate["messages"])
            for candidate in enriched if candidate["task"] == row["task"])
        for row in enriched
    }
    normalized = []
    for row in rows:
        if id(row) in public_ids:
            normalized.append(next(public_converted))
        elif id(row) in current_ids:
            normalized.append(_current_canonical(row))
        elif id(row) in enriched_ids:
            normalized.append(_legacy_enriched(
                row, enriched_lengths[row["task"]]))
        else:
            normalized.append(next(historical_converted))
    return normalized


def _migrate_recognized_stream(path: Path) -> tuple[int, int] | None:
    """Normalize large published files in two bounded-memory passes."""
    final: dict[tuple[str, str], tuple[int, str, int]] = {}
    contaminated_tasks: set[str] = set()
    count = 0
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = sanitize_object(json.loads(line))
            generation = _generation(row)
            if generation is None:
                return None
            if _contains_internal_control(row):
                contaminated_tasks.add(str(row["task"]))
            count += 1
            if generation in {"public", "enriched"}:
                identity = str(row["task"])
            elif generation in {"historical", "current"}:
                identity = str(row["source_trajectory_id"])
            else:
                continue
            step = int(row["assistant_step"])
            prior = final.get((generation, identity))
            if prior is None or step > prior[0]:
                final[(generation, identity)] = (
                    step, _source_hash(row), len(row["messages"]))
    if not count:
        raise ValueError("dataset is empty")
    public_hashes = {
        identity: value[1] for (generation, identity), value in final.items()
        if generation == "public"}
    public_lengths = {
        identity: value[2] for (generation, identity), value in final.items()
        if generation == "public"}
    enriched_lengths = {
        identity: value[2] for (generation, identity), value in final.items()
        if generation == "enriched"}
    historical_hashes = {
        identity: value[1] for (generation, identity), value in final.items()
        if generation == "historical"}
    current_hashes = {
        identity: value[1] for (generation, identity), value in final.items()
        if generation == "current"}
    backup = path.with_name(path.name + ".pre-normalized")
    if not backup.exists():
        shutil.copy2(path, backup)
    pending = path.with_suffix(path.suffix + ".canonical.pending")
    with path.open() as source, pending.open("x") as destination:
        for line in source:
            if not line.strip():
                continue
            row = sanitize_object(json.loads(line))
            if str(row["task"]) in contaminated_tasks:
                continue
            generation = _generation(row)
            if generation == "public":
                normalized = _legacy_public(
                    [row], public_hashes, public_lengths)[0]
            elif generation == "enriched":
                normalized = _legacy_enriched(
                    row, enriched_lengths[str(row["task"])])
            elif generation == "historical":
                normalized = _historical_canonical([row], historical_hashes)[0]
            else:
                normalized = _current_canonical(
                    row, current_hashes[str(row["source_trajectory_id"])])
            normalized = _privacy_scrub_row(normalized)
            destination.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        destination.flush()
        os.fsync(destination.fileno())
    validation = validate_export(pending)
    pending.replace(path)
    _advance_baseline(path, validation)
    return validation["trajectories"], validation["rows"]


def migrate(path: Path) -> tuple[int, int]:
    streamed = _migrate_recognized_stream(path)
    if streamed is not None:
        return streamed
    rows = [sanitize_object(json.loads(line))
            for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("dataset is empty")
    contaminated_tasks = {
        str(row["task"]) for row in rows if _contains_internal_control(row)}
    rows = [row for row in rows if str(row["task"]) not in contaminated_tasks]
    if not rows:
        raise ValueError("dataset contains no uncontaminated trajectories")
    if any("source_trajectory_id" in row or "assistant_step" in row for row in rows):
        raise ValueError("dataset mixes canonical and non-canonical rows")

    val_fraction = float((CONFIG.get("build") or {}).get("val_frac", 0.08))
    converted = []
    for row in rows:
        runtime = str(row.get("teacher_runtime") or "")
        trace_format = str(row.get("trace_format") or "")
        messages = row.get("messages") or []
        used = sorted({call.get("function", {}).get("name")
                       for message in messages
                       for call in (message.get("tool_calls") or [])
                       if call.get("function", {}).get("name")})
        record = {"messages": messages, "meta": {
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

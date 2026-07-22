"""Append-oriented Parquet artifacts for the canonical Moonshiner row stream."""
from __future__ import annotations

import base64
import json
from pathlib import Path

MANIFEST = "dataset-manifest.json"
FORMAT = "moonshiner-parquet-shards-v1"


def _arrow():
    try:
        import pyarrow as pa
        import pyarrow.json as paj
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError(
            "Parquet publication requires pyarrow; reinstall Moonshiner") from error
    return pa, paj, pq


def _load_manifest(root: Path) -> dict | None:
    path = root / MANIFEST
    if not path.is_file():
        return None
    value = json.loads(path.read_text())
    if value.get("schema_version") != 1 or value.get("format") != FORMAT:
        raise ValueError("unsupported Parquet publication manifest")
    return value


def _schema_text(schema) -> str:
    return base64.b64encode(schema.serialize().to_pybytes()).decode("ascii")


def _schema_from_text(value: str):
    pa, _, _ = _arrow()
    return pa.ipc.read_schema(pa.BufferReader(base64.b64decode(value)))


def _describe(value):
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool",)
    if isinstance(value, int):
        return ("int",)
    if isinstance(value, float):
        return ("float",)
    if isinstance(value, str):
        return ("string",)
    if isinstance(value, list):
        item = ("null",)
        for value_item in value:
            item = _merge_descriptions(item, _describe(value_item))
        return ("list", item)
    if isinstance(value, dict):
        return ("struct", {key: _describe(item) for key, item in value.items()})
    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")


def _merge_descriptions(left, right, path="row"):
    if left[0] == "json" or right[0] == "json":
        return ("json",)
    if left[0] == "null":
        return right
    if right[0] == "null":
        return left
    if {left[0], right[0]} == {"int", "float"}:
        return ("float",)
    if left[0] != right[0]:
        return ("json",)
    if left[0] == "list":
        return ("list", _merge_descriptions(left[1], right[1], f"{path}[]"))
    if left[0] == "struct":
        fields = dict(left[1])
        for key, value in right[1].items():
            fields[key] = (_merge_descriptions(fields[key], value, f"{path}.{key}")
                           if key in fields else value)
        return ("struct", fields)
    return left


def _arrow_type(description):
    pa, _, _ = _arrow()
    kind = description[0]
    scalars = {"null": pa.null(), "bool": pa.bool_(), "int": pa.int64(),
               "float": pa.float64(), "string": pa.string(),
               "json": pa.string()}
    if kind in scalars:
        return scalars[kind]
    if kind == "list":
        return pa.list_(_arrow_type(description[1]))
    if kind == "struct":
        return pa.struct([pa.field(key, _arrow_type(value))
                          for key, value in description[1].items()])
    raise ValueError(f"unsupported schema description: {kind}")


def _discover_schema(source: Path):
    pa, _, _ = _arrow()
    description = ("null",)
    with source.open() as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {number}: row must be an object")
            description = _merge_descriptions(description, _describe(value))
    if description[0] != "struct":
        raise ValueError("canonical JSONL contains no rows")
    return (pa.schema([pa.field(key, _arrow_type(value))
                       for key, value in description[1].items()]), description)


def _normalize(value, description):
    kind = description[0]
    if kind == "json":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return None
    if kind == "list":
        return [_normalize(item, description[1]) for item in value]
    if kind == "struct":
        return {key: _normalize(value.get(key), item)
                for key, item in description[1].items() if key in value}
    return value


def _normalized_source(source: Path, description) -> Path:
    pending = source.with_name(".parquet-source.pending.jsonl")
    with source.open() as input_handle, pending.open("w") as output_handle:
        for line in input_handle:
            if not line.strip():
                continue
            row = _normalize(json.loads(line), description)
            output_handle.write(json.dumps(
                row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return pending


def _read_rows(source: Path, schema=None):
    _, paj, _ = _arrow()
    discovered, description = _discover_schema(source)
    if schema is not None and not discovered.equals(schema):
        raise ValueError("canonical row schema changed across Parquet shards")
    schema = schema or discovered
    normalized = _normalized_source(source, description)
    read = paj.ReadOptions(block_size=64 * 1024 * 1024)
    parse = paj.ParseOptions(explicit_schema=schema,
                             unexpected_field_behavior="error")
    reader = paj.open_json(normalized, read_options=read, parse_options=parse)
    return reader.schema, reader, normalized


def _write_shard(root: Path, sequence: int, rows: list[dict], schema) -> dict:
    pa, _, pq = _arrow()
    relative = f"data/train-{sequence:05d}.parquet"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(".parquet.pending")
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, pending, compression="zstd", use_dictionary=True)
    pending.replace(destination)
    tasks = list(dict.fromkeys(str(row["task"]) for row in rows))
    return {"path": relative, "tasks": tasks, "trajectory_count": len(tasks),
            "row_count": len(rows), "bytes": destination.stat().st_size}


def _chunks_by_task(rows: list[dict], size: int):
    groups: dict[str, list[dict]] = {}
    order = []
    for row in rows:
        task = str(row["task"])
        if task not in groups:
            groups[task] = []
            order.append(task)
        groups[task].append(row)
    for offset in range(0, len(order), size):
        tasks = order[offset:offset + size]
        yield [row for task in tasks for row in groups[task]]


def sync(source: Path, root: Path, *, changed_tasks: set[str],
         trajectories_per_shard: int = 10) -> dict:
    """Update the active immutable shard set from canonical JSONL rows.

    Replacements retire the affected active shard logically and carry its
    unaffected neighbors into a new shard. Retired shard bytes remain locally
    and remotely recoverable but are absent from the active manifest/card.
    """
    if trajectories_per_shard < 1:
        raise ValueError("trajectories_per_shard must be positive")
    _, _, pq = _arrow()
    root.mkdir(parents=True, exist_ok=True)
    existing = _load_manifest(root)
    active = list((existing or {}).get("shards") or [])
    task_to_shard = {task: shard for shard in active for task in shard["tasks"]}
    impacted_paths = {task_to_shard[task]["path"] for task in changed_tasks
                      if task in task_to_shard}
    rebuild_tasks = set(changed_tasks)
    for shard in active:
        if shard["path"] in impacted_paths:
            rebuild_tasks.update(shard["tasks"])

    stored_schema = ((existing or {}).get("arrow_schema") or None)
    schema, batches, normalized = _read_rows(
        source, _schema_from_text(stored_schema) if stored_schema else None)
    if stored_schema and _schema_text(schema) != stored_schema:
        raise ValueError("canonical row schema changed across Parquet shards")
    selected = []
    all_tasks = set()
    total_rows = 0
    try:
        for batch in batches:
            for row in batch.to_pylist():
                task = str(row["task"])
                all_tasks.add(task)
                total_rows += 1
                if existing is None or task in rebuild_tasks:
                    selected.append(row)
    finally:
        normalized.unlink(missing_ok=True)
    missing = rebuild_tasks - all_tasks
    if missing:
        raise ValueError(f"changed trajectories missing from canonical rows: {sorted(missing)}")

    kept = [shard for shard in active if shard["path"] not in impacted_paths]
    sequence = int((existing or {}).get("next_sequence", 0))
    added = []
    for rows in _chunks_by_task(selected, trajectories_per_shard):
        added.append(_write_shard(root, sequence, rows, schema))
        sequence += 1
    shards = kept + added
    active_task_list = [task for shard in shards for task in shard["tasks"]]
    active_tasks = set(active_task_list)
    if len(active_task_list) != len(active_tasks):
        raise ValueError("a trajectory appears in more than one active Parquet shard")
    if active_tasks != all_tasks:
        raise ValueError("active Parquet shards do not cover canonical trajectories exactly")
    if sum(int(item["row_count"]) for item in shards) != total_rows:
        raise ValueError("active Parquet shard row count differs from canonical rows")
    for shard in shards:
        path = root / shard["path"]
        if not path.is_file() or path.stat().st_size != int(shard["bytes"]):
            raise ValueError(f"active Parquet shard is missing or changed: {shard['path']}")
        if not pq.read_schema(path).equals(schema):
            raise ValueError(f"active Parquet shard schema differs: {shard['path']}")
    manifest = {
        "schema_version": 1, "format": FORMAT,
        "arrow_schema": _schema_text(schema), "next_sequence": sequence,
        "active_shards": [item["path"] for item in shards], "shards": shards,
        "trajectory_count": len(all_tasks), "row_count": total_rows,
        "bytes": sum(item["bytes"] for item in shards),
    }
    pending = root / f"{MANIFEST}.pending"
    pending.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    pending.replace(root / MANIFEST)
    return manifest


def read_active_rows(root: Path) -> list[dict]:
    _, _, pq = _arrow()
    manifest = _load_manifest(root)
    if not manifest:
        return []
    rows = []
    for relative in manifest["active_shards"]:
        rows.extend(pq.read_table(root / relative).to_pylist())
    return rows

"""Import pre-Moonshiner traces or prepared HF rows and resume by task ID."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from common import DATA, SECRET_RE, STORAGE_ROOT, TRACES
from privacy import sanitize_object

INDEX = TRACES / "imported_index.json"


def _slug(value: str) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value).strip("-")
    return "-".join(filter(None, cleaned.split("-")))[:80] or "import"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_immutable(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha(source) != _sha(destination):
            raise RuntimeError(f"refusing conflicting imported artifact: {destination}")
        return False
    shutil.copy2(source, destination)
    return True


def _load_index() -> dict:
    if not INDEX.exists():
        return {"schema_version": 1, "sources": {}, "task_ids": []}
    return json.loads(INDEX.read_text())


def imported_task_ids() -> set[str]:
    task_ids = set(_load_index().get("task_ids", []))
    published = DATA / "hf-sync" / "published-trajectories.json"
    if published.is_file():
        try:
            task_ids.update(json.loads(published.read_text()).get(
                "published_tasks", []))
        except (OSError, json.JSONDecodeError):
            pass
    return task_ids


def _safe_row(row: dict) -> dict:
    clean = sanitize_object(row)
    serialized = json.dumps(clean, ensure_ascii=False)
    serialized = SECRET_RE.sub("[REDACTED_SECRET]", serialized)
    clean = json.loads(serialized)
    if SECRET_RE.search(json.dumps(clean, ensure_ascii=False)):
        raise RuntimeError("secret-shaped value survived import sanitization")
    return clean


def _task_id(row: dict) -> str | None:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    for value in (meta.get("task"), row.get("task"), row.get("seed_id"), row.get("id")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _import_prepared(directory: Path, source_slug: str) -> tuple[int, set[str]]:
    output = DATA / "imported" / source_slug / "rows.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, ids, seen = [], set(), set()
    if output.exists():
        for line in output.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append(row)
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            seen.add(meta.get("import_fingerprint") or
                     hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest())
            ids.add(_task_id(row) or "")
    for path in sorted(directory.rglob("*.jsonl")):
        # Raw runtime streams are handled as artifacts, not training rows.
        if any(part in {"raw", "reviews", "meta", "diffs"} for part in path.parts):
            continue
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
                continue
            row = _safe_row(value)
            fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            task_id = _task_id(row) or f"imported-{fingerprint[:16]}"
            meta = row.setdefault("meta", {})
            if not isinstance(meta, dict):
                raise RuntimeError(f"{path}:{lineno}: meta must be an object")
            meta.setdefault("task", task_id)
            meta.setdefault("imported_source", source_slug)
            meta.setdefault("import_fingerprint", fingerprint)
            rows.append(row)
            ids.add(task_id)
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    output.write_text(payload)
    ids.discard("")
    return len(rows), ids


def _import_artifacts(directory: Path) -> tuple[int, set[str]]:
    copied, ids = 0, set()
    roots = [directory]
    roots.extend(p for p in directory.rglob("traces") if p.is_dir())
    for root in roots:
        for kind, suffix in (("raw", "*.jsonl"), ("meta", "*.json"),
                             ("reviews", "*.json"), ("diffs", "*.patch")):
            source_dir = root / kind
            if not source_dir.is_dir():
                continue
            for source in sorted(source_dir.glob(suffix)):
                copied += _copy_immutable(source, TRACES / kind / source.name)
                if kind == "meta":
                    try:
                        value = json.loads(source.read_text())
                        if isinstance(value.get("id"), str) and value.get("passed") is True:
                            ids.add(value["id"])
                    except json.JSONDecodeError:
                        pass
    return copied, ids


def import_directory(directory: Path, label: str, *, identity: str | None = None) -> dict:
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    source_slug = _slug(label)
    artifact_count, artifact_ids = _import_artifacts(directory)
    row_count, row_ids = _import_prepared(directory, source_slug)
    ids = artifact_ids | row_ids
    record = {"source": label, "path": identity or str(directory), "artifacts": artifact_count,
              "prepared_rows": row_count, "task_ids": sorted(ids)}
    index = _load_index()
    previous = index["sources"].get(source_slug)
    if previous and previous.get("path") != record.get("path"):
        raise RuntimeError(f"source label {source_slug!r} belongs to a different path")
    index["sources"][source_slug] = record
    index["task_ids"] = sorted(set(index.get("task_ids", [])) | ids)
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner trace import")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--directory", type=Path)
    source.add_argument("--hf", help="Hugging Face dataset repository ID")
    parser.add_argument("--revision", help="Pinned HF branch, tag, or commit")
    parser.add_argument("--label", help="Stable local source label")
    args = parser.parse_args(argv)
    if args.directory:
        directory, label = args.directory, args.label or args.directory.name
        record = import_directory(directory, label)
    else:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise SystemExit("HF import requires: pip install 'moonshiner[huggingface]'") from exc
        with tempfile.TemporaryDirectory(prefix="moonshiner-hf-import-") as temporary:
            local = snapshot_download(repo_id=args.hf, repo_type="dataset",
                                      revision=args.revision, local_dir=temporary)
            identity = f"hf:{args.hf}@{args.revision or 'latest'}"
            record = import_directory(Path(local), args.label or args.hf,
                                      identity=identity)
            record["hf_repo"] = args.hf
            record["revision"] = args.revision
    print(json.dumps(record, indent=2))
    print(f"resume index now contains {len(imported_task_ids())} completed task id(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

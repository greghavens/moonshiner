"""Manifest helpers for the incremental backup agent.

The agent walks a tree, asks scan_changes() what needs a fresh copy, uploads
those files, then records them with record_backup(). Restore tooling calls
restore_order() to know the sequence in which recorded files are applied.

Timestamps in `backed_up_at` are ISO-8601 with a UTC offset; agents in
different regions stamp with their local offset.
"""
import json
import os
from datetime import datetime

ISO_FMT = "%Y-%m-%dT%H:%M:%S"


def load_manifest(path):
    if not os.path.exists(path):
        return {"entries": {}}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_manifest(path, manifest):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _walk_files(root):
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found.append(rel)
    return sorted(found)


def file_state(root, rel):
    st = os.stat(os.path.join(root, rel))
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def needs_backup(entry, state):
    if entry is None:
        return True
    return entry["size"] != state["size"] or entry["mtime"] != state["mtime"]


def scan_changes(root, manifest):
    """Relative paths under root that are new or changed since the manifest."""
    changed = []
    for rel in _walk_files(root):
        state = file_state(root, rel)
        if needs_backup(manifest["entries"].get(rel), state):
            changed.append(rel)
    return changed


def record_backup(manifest, root, rel, backed_up_at):
    """Mark rel as backed up at `backed_up_at` (ISO-8601 with offset)."""
    state = file_state(root, rel)
    manifest["entries"][rel] = {
        "size": state["size"],
        "mtime": state["mtime"],
        "backed_up_at": backed_up_at,
    }


def restore_order(manifest):
    """Paths in apply order: oldest backup first, ties broken by path."""
    def sort_key(item):
        rel, entry = item
        stamped = datetime.strptime(entry["backed_up_at"][:19], ISO_FMT)
        return (stamped, rel)

    ordered = sorted(manifest["entries"].items(), key=sort_key)
    return [rel for rel, _entry in ordered]

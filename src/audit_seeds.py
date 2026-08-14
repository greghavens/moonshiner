#!/usr/bin/env python3
"""Integrity audit for the tracked seed corpus.

A seed is COMPLETE when its ``task.json`` parses, carries an ``id`` matching its
directory name, a ``category``, and a ``prompt``, and honours whatever else it
declares: a seed naming ``test_files`` ships them under ``files/``, and a seed
naming a ``verify_cmd`` ships a non-empty ``reference_fix.patch`` proving local
solvability. Holdout tasks are patch-exempt: they are vetted by held-out
evaluation, not by a shipped reference fix.

A seed is a seed. ``category`` is reference, not a loading type, so this audits
the whole corpus through one path rather than sorting seeds into kinds.

A partial seed (an authoring agent that died mid-write) poisons trace batches
and blocks re-import, so this prints one line per seed and exits non-zero if any
are partial. Deletion is a human decision — this only reports.

Model-free.
  python3 src/audit_seeds.py
  python3 src/audit_seeds.py --ids   # also emit complete ids / partial dirs
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
import sys
from pathlib import Path

from common import BEHAVIOR_WORLDS, CONFIG, SEEDS_DIR

SIMULATION_CONTRACT = {"mode": "deterministic_simulation",
                       "external_side_effects": False,
                       "live_network": False, "real_credentials": False}
EXPECTED_KEYS = {"decision", "clarification", "stages", "state_assertions",
                 "forbidden_tools", "response_constraints"}
STAGE_KEYS = {"parallel", "calls", "purpose"}
# Dataset rows describe the capability under test. External benchmark names are
# never valid seed content, metadata, or training labels.
FORBIDDEN_BENCHMARK = "b" + "fcl"


def world_registry() -> dict:
    return json.loads(BEHAVIOR_WORLDS.read_text())

# Every seed carries these, and nothing else is universal. Seeds differ in what
# they declare, not in what they are: a seed that declares test files must ship
# them, a seed that declares a verify command must prove it passes. Auditing
# what is declared keeps one code path over the whole corpus.
REQUIRED = ("id", "category", "prompt")
# Pre-spec pilot seeds predate the reference-patch requirement; their
# solvability is proven by actual passing teacher traces, not a shipped fix.
PILOT_EXEMPT = {"py-lru-eviction", "py-config-merge", "go-worker-pool",
                "ts-pagination"}
PATCH_EXEMPT = PILOT_EXEMPT | set(CONFIG.get("holdout_tasks", []))
SEED_ENTRIES = {"task.json", "files", "reference_fix.patch"}
CAPABILITY_FIELDS = ("required_harness_capabilities",
                     "preferred_harness_capabilities")


def check(directory: Path, worlds: dict | None = None) -> str | None:
    """Return a reason string if the seed is partial, else None."""
    extra = sorted(path.name for path in directory.iterdir()
                   if path.name not in SEED_ENTRIES)
    if extra:
        return f"contains forbidden non-seed state: {extra}"
    task_path = directory / "task.json"
    if not task_path.exists():
        return "no task.json"
    try:
        serialized = task_path.read_text()
        task = json.loads(serialized)
    except json.JSONDecodeError as error:
        return f"task.json invalid: {error}"
    if FORBIDDEN_BENCHMARK in serialized.casefold():
        return "contains a forbidden benchmark name"
    for field in CAPABILITY_FIELDS:
        if field not in task:
            continue
        value = task[field]
        if (not isinstance(value, list)
                or any(not isinstance(item, str) or not item.strip()
                       for item in value)):
            return f"task.json {field} must be a list of nonempty strings"
    missing = [key for key in REQUIRED if not task.get(key)]
    if missing:
        return f"task.json missing {missing}"
    if task["id"] != directory.name:
        return f"id {task['id']!r} != dir name"
    if task.get("test_files"):
        files = directory / "files"
        if not files.is_dir():
            return "no files/"
        absent = [name for name in task["test_files"] if not (files / name).exists()]
        if absent:
            return f"test files absent: {absent}"
    if task.get("verify_cmd") and directory.name not in PATCH_EXEMPT:
        patch = directory / "reference_fix.patch"
        if not patch.exists() or patch.stat().st_size == 0:
            return "reference_fix.patch missing/empty"

    # ``expected`` is what states the simulated-tool contract; ``world`` alone is
    # a domain label that code-contract seeds also carry. A seed stating that
    # contract must be consistent with its world: every tool it offers or expects
    # has to exist there, and nothing it expects may also be forbidden. Same rule
    # as above — audit what the seed declares.
    expected = task.get("expected")
    if expected is not None:
        if not isinstance(expected, dict):
            return "expected must be an object"
        world = (worlds if worlds is not None else world_registry()["worlds"]
                 ).get(task.get("world"))
        if not world:
            return f"unknown world {task.get('world')!r}"
        extra = sorted(set(expected) - EXPECTED_KEYS)
        if extra:
            return f"unexpected expected fields {extra}"
        for index, stage in enumerate(expected.get("stages", [])):
            if not isinstance(stage, dict) or set(stage) - STAGE_KEYS:
                return f"invalid stage {index}"
            for position, call in enumerate(stage.get("calls", [])):
                if (not isinstance(call, dict) or set(call) != {"tool", "arguments"}
                        or not isinstance(call.get("arguments"), dict)):
                    return f"invalid call {index}.{position}"
            if stage.get("parallel") and len(stage.get("calls", [])) < 2:
                return f"parallel stage {index} has fewer than two calls"
        known = {item["name"] for item in world["tools"]}
        introduced = {name for turn in task.get("follow_up_turns", [])
                      for name in turn.get("add_tools", [])}
        available = set(task.get("available_tools", []))
        unknown = (available | introduced) - known
        if unknown:
            return f"unknown tools {sorted(unknown)}"
        wanted = {call["tool"] for stage in expected.get("stages", [])
                  for call in stage.get("calls", [])}
        unavailable = wanted - available - introduced
        if unavailable:
            return f"expected unavailable tools {sorted(unavailable)}"
        conflict = wanted & set(expected.get("forbidden_tools", []))
        if conflict:
            return f"expected/forbidden conflict {sorted(conflict)}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", action="store_true",
                        help="Also print complete ids and partial dirs")
    args = parser.parse_args(argv)

    if not SEEDS_DIR.is_dir():
        print(f"no seed corpus at {SEEDS_DIR} — run src/import_seeds.py first",
              file=sys.stderr)
        return 1

    registry = world_registry()
    worlds = registry["worlds"]
    complete, partial = [], []
    if (registry.get("execution_contract") or {}) != SIMULATION_CONTRACT:
        partial.append(("<world registry>", "lacks the required "
                        "non-destructive simulation contract"))

    seen: dict[str, str] = {}
    tags: Counter[str] = Counter()
    simulated = 0
    for directory in sorted(p for p in SEEDS_DIR.iterdir() if p.is_dir()):
        why = check(directory, worlds)
        if not why:
            try:
                task = json.loads((directory / "task.json").read_text())
            except (OSError, json.JSONDecodeError):
                task = {}
            duplicate = seen.get(str(task.get("id")))
            if duplicate:
                why = f"duplicate id, also declared by {duplicate}"
            else:
                seen[str(task.get("id"))] = directory.name
                tags.update(task.get("training_tags") or [])
                simulated += bool(task.get("world"))
        (partial if why else complete).append((directory.name, why))
    for name, _ in complete:
        print(f"[complete] {name}")
    for name, why in partial:
        print(f"[PARTIAL ] {name}: {why}")
    print(f"\n{len(complete)} complete, {len(partial)} partial")
    # Corpus composition, reported rather than asserted: authoring-programme
    # totals describe a plan in progress, so they must not fail an integrity
    # audit that gates committing the very seeds that would complete it.
    print(f"corpus: {simulated} simulated-world seeds; "
          f"round:2 {tags.get('round:2', 0)}; "
          f"breadth-reserve {tags.get('source:breadth-reserve', 0)}; "
          f"benchmark-informed {tags.get('source:benchmark-informed', 0)}")
    if args.ids:
        print("complete-ids:", ",".join(name for name, _ in complete))
        print("partial-dirs:",
              " ".join(str(SEEDS_DIR / name) for name, _ in partial))
    return 1 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())

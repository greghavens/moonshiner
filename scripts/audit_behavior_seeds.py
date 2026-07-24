#!/usr/bin/env python3
"""Offline structural and behavioral audit for non-code tool-use seeds."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from common import BEHAVIOR_WORLDS, SEEDS_DIR  # noqa: E402

SEEDS = SEEDS_DIR
SCHEMA = ROOT / "schemas" / "behavior_seed.schema.json"
WORLDS = BEHAVIOR_WORLDS


def audit() -> tuple[list[str], dict]:
    errors = []
    schema = json.loads(SCHEMA.read_text())
    world_document = json.loads(WORLDS.read_text())
    contract = world_document.get("execution_contract") or {}
    if contract != {"mode": "deterministic_simulation", "external_side_effects": False,
                    "live_network": False, "real_credentials": False}:
        errors.append("world registry lacks the required non-destructive simulation contract")
    worlds = world_document["worlds"]
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    categories, tags, domains = Counter(), Counter(), Counter()
    ids, prompts = set(), set()
    parallel = 0
    required = set(schema["required"])
    top_allowed = set(schema["properties"])
    for path in sorted(SEEDS.glob("behavior-*/task.json")):
        try:
            serialized = path.read_text()
            seed = json.loads(serialized)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        # Dataset rows describe the capability under test. External benchmark
        # names are never valid seed content, metadata, or training labels.
        forbidden_benchmark = "b" + "fcl"
        if forbidden_benchmark in serialized.casefold():
            errors.append(f"{path.name}: contains a forbidden benchmark name")
        if jsonschema:
            try:
                jsonschema.validate(seed, schema)
            except jsonschema.ValidationError as exc:
                errors.append(f"{path.name}: schema: {exc.message}")
        else:
            missing = sorted(required - set(seed))
            if missing:
                errors.append(f"{path.name}: missing required fields {missing}")
            if seed.get("schema_version") != 1 or seed.get("kind") != "tool_behavior":
                errors.append(f"{path.name}: invalid schema_version or kind")
            extra = sorted(set(seed) - top_allowed)
            if extra:
                errors.append(f"{path.name}: unexpected fields {extra}")
        expected_value = seed.get("expected")
        if not isinstance(expected_value, dict):
            errors.append(f"{path.name}: expected must be an object")
            continue
        expected_allowed = {"decision", "clarification", "stages", "state_assertions",
                            "forbidden_tools", "response_constraints"}
        extra = sorted(set(expected_value) - expected_allowed)
        if extra:
            errors.append(f"{path.name}: unexpected expected fields {extra}")
        for stage_index, stage in enumerate(expected_value.get("stages", [])):
            if not isinstance(stage, dict) or set(stage) - {"parallel", "calls", "purpose"}:
                errors.append(f"{path.name}: invalid stage {stage_index}")
                continue
            for call_index, call in enumerate(stage.get("calls", [])):
                if (not isinstance(call, dict) or set(call) != {"tool", "arguments"}
                        or not isinstance(call.get("arguments"), dict)):
                    errors.append(f"{path.name}: invalid call {stage_index}.{call_index}")
        sid = seed.get("id")
        if sid != path.stem:
            errors.append(f"{path.name}: id does not match filename")
        if sid in ids:
            errors.append(f"{path.name}: duplicate id {sid}")
        ids.add(sid)
        # Identical user wording against different backend states is useful for
        # state-grounding. Count it for visibility, but do not reject it.
        prompts.add(seed.get("prompt"))
        world = worlds.get(seed.get("world"))
        if not world:
            errors.append(f"{path.name}: unknown world {seed.get('world')}")
            continue
        known = {item["name"] for item in world["tools"]}
        introduced = {name for turn in seed.get("follow_up_turns", [])
                      for name in turn.get("add_tools", [])}
        available = set(seed.get("available_tools", []))
        unknown = (available | introduced) - known
        if unknown:
            errors.append(f"{path.name}: unknown tools {sorted(unknown)}")
        expected_tools = {call["tool"] for stage in seed["expected"]["stages"]
                          for call in stage["calls"]}
        unavailable = expected_tools - available - introduced
        if unavailable:
            errors.append(f"{path.name}: expected unavailable tools {sorted(unavailable)}")
        forbidden = set(seed["expected"]["forbidden_tools"])
        conflict = expected_tools & forbidden
        if conflict:
            errors.append(f"{path.name}: expected/forbidden conflict {sorted(conflict)}")
        for stage in seed["expected"]["stages"]:
            if stage["parallel"]:
                parallel += 1
                if len(stage["calls"]) < 2:
                    errors.append(f"{path.name}: parallel stage has fewer than two calls")
        categories[seed["category"]] += 1
        domains[seed["world"]] += 1
        tags.update(seed["training_tags"])
    report = {"seed_count": len(ids), "unique_prompts": len(prompts),
              "category_counts": dict(sorted(categories.items())),
              "world_counts": dict(sorted(domains.items())),
              "tag_counts": dict(sorted(tags.items())), "parallel_stages": parallel,
              "jsonschema_validation": jsonschema is not None}
    if len(ids) != 2000:
        errors.append(f"expected exactly 2000 seeds, found {len(ids)}")
    if tags.get("round:2") != 1000:
        errors.append(f"expected exactly 1000 round:2 seeds, found {tags.get('round:2', 0)}")
    if tags.get("source:breadth-reserve") != 400:
        errors.append("Round 2 breadth reserve must contain exactly 400 seeds")
    if tags.get("source:benchmark-informed") != 600:
        errors.append("Round 2 benchmark-informed allocation must contain exactly 600 seeds")
    return errors, report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors, report = audit()
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors, **report}, indent=2))
    else:
        print(f"{report['seed_count']} behavior seeds; {len(report['category_counts'])} categories; {report['parallel_stages']} parallel stages")
        for category, count in report["category_counts"].items():
            print(f"  {category}: {count}")
        for error in errors:
            print(f"[ERROR] {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

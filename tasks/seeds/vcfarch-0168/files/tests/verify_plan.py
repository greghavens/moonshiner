from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "build" / "vcf-migration-plan.json"
SCHEMA_PATH = ROOT / "spec" / "migration-plan.schema.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate.json"
SNAPSHOT_PATH = ROOT / "fixtures" / "compatibility-snapshot.json"
RESEARCH_PATH = ROOT / "research.json"


class VerificationError(AssertionError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing required file: {path.relative_to(ROOT)}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")


def type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    fail(f"verifier does not understand schema type {expected!r}")


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        fail(f"unsupported non-local schema reference: {ref}")
    node: Any = root_schema
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            fail(f"broken schema reference: {ref}")
        node = node[token]
    if not isinstance(node, dict):
        fail(f"schema reference does not resolve to an object: {ref}")
    return node


def validate_schema(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> None:
    """Validate the complete schema subset used by the installer specification."""
    if "$ref" in schema:
        validate_schema(instance, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema and instance != schema["const"]:
        fail(f"schema {path}: expected constant {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(f"schema {path}: {instance!r} is not one of {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(type_matches(instance, choice) for choice in choices):
            fail(f"schema {path}: expected type {choices!r}, got {type(instance).__name__}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            fail(f"schema {path}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if key in properties:
                validate_schema(value, properties[key], root_schema, child_path)
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    fail(f"schema {path}: unexpected property {key!r}")
                if isinstance(additional, dict):
                    validate_schema(value, additional, root_schema, child_path)

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            fail(f"schema {path}: expected at least {schema['minItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(encoded) != len(set(encoded)):
                fail(f"schema {path}: array items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                validate_schema(value, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            fail(f"schema {path}: string is shorter than {schema['minLength']}")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            fail(f"schema {path}: {instance!r} does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail(f"schema {path}: {instance!r} is below minimum {schema['minimum']!r}")


def require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        fail(f"{label}: expected {expected!r}, got {actual!r}")


def flattened_inventory_content(inventory: dict[str, Any]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for source in inventory["sources"]:
        for item in source["content"]:
            if item["id"] in result:
                fail(f"fixture contains duplicate content id {item['id']!r}")
            result[item["id"]] = (source["id"], item["kind"])
    return result


def verify_sources(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    planned: dict[str, dict[str, Any]] = {}
    for source in plan["sources"]:
        if source["source_id"] in planned:
            fail(f"duplicate source id {source['source_id']!r}")
        planned[source["source_id"]] = source
    fixture = {source["id"]: source for source in inventory["sources"]}
    require_equal(len(plan["sources"]), len(inventory["sources"]), "source count")
    require_equal(set(planned), set(fixture), "source coverage")
    target_date = date.fromisoformat(inventory["target"]["release_date"])
    ended_before = 0
    for source_id, estate_source in fixture.items():
        rule = snapshot["product_rules"][source_id]
        item = planned[source_id]
        require_equal(item["product"], estate_source["product"], f"{source_id} product")
        require_equal(item["version"], estate_source["version"], f"{source_id} version")
        for field in ("source_support_ends", "target_component", "target_version", "transition_mode"):
            require_equal(item[field], rule[field], f"{source_id} {field}")
        require_equal(item["version_path"], rule["version_path"], f"{source_id} version path")
        if date.fromisoformat(item["source_support_ends"]) < target_date:
            ended_before += 1
            require_equal(item["support_state_at_target"], "ended_before_target", f"{source_id} support state")
    if ended_before == 0:
        fail("the architecture does not expose the required pre-target support boundary")


def verify_content(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected_items = flattened_inventory_content(inventory)
    dispositions: dict[str, dict[str, Any]] = {}
    for item in plan["content_dispositions"]:
        if item["item_id"] in dispositions:
            fail(f"duplicate disposition for {item['item_id']!r}")
        dispositions[item["item_id"]] = item
    require_equal(set(dispositions), set(expected_items), "content disposition coverage")
    require_equal(set(snapshot["content_rules"]), set(expected_items), "snapshot content authority coverage")
    for item_id, (source_id, kind) in expected_items.items():
        actual = dispositions[item_id]
        rule = snapshot["content_rules"][item_id]
        require_equal(actual["source_id"], source_id, f"{item_id} source")
        require_equal(actual["kind"], kind, f"{item_id} kind")
        for field in ("disposition", "method", "target_or_treatment"):
            require_equal(actual[field], rule[field], f"{item_id} {field}")


def verify_placements(plan: dict[str, Any], inventory: dict[str, Any], snapshot: dict[str, Any]) -> None:
    placements: dict[str, dict[str, Any]] = {}
    for item in plan["placements"]:
        if item["component"] in placements:
            fail(f"duplicate placement for {item['component']!r}")
        placements[item["component"]] = item
    rules = snapshot["placement_rules"]
    require_equal(len(plan["placements"]), len(rules), "target component placement count")
    require_equal(set(placements), set(rules), "target component placement coverage")
    target = inventory["target"]
    measured = inventory["measured_load"]
    for component, rule in rules.items():
        placement = placements[component]
        require_equal(placement["version"], snapshot["target_release"], f"{component} target version")
        for field in ("preset", "node_count", "vcpu_per_node", "memory_gib_per_node", "rated_capacity"):
            require_equal(placement[field], rule[field], f"{component} {field}")
        for field in ("site", "workload_domain", "network", "datastore"):
            require_equal(placement[field], target[field], f"{component} {field}")
        require_equal(placement["design_load"], measured, f"{component} measured design load")
        if len(placement["fault_domains"]) < rule["minimum_fault_domains"]:
            fail(f"{component} does not span the pinned minimum fault-domain count")
        if not set(placement["fault_domains"]).issubset(set(target["fault_domains"])):
            fail(f"{component} uses a fault domain absent from the estate fixture")
        for metric, capacity in rule["rated_capacity"].items():
            if capacity and capacity < measured[metric]:
                fail(f"{component} pinned capacity for {metric} is below demand")


def transition_key(source_id: str, from_version: str, to_version: str) -> str:
    return f"{source_id}:{from_version}->{to_version}"


def verify_steps(plan: dict[str, Any], snapshot: dict[str, Any]) -> None:
    steps = plan["steps"]
    require_equal([step["sequence"] for step in steps], list(range(1, len(steps) + 1)), "step sequence")
    by_id: dict[str, dict[str, Any]] = {}
    for step in steps:
        if step["id"] in by_id:
            fail(f"duplicate step id {step['id']!r}")
        by_id[step["id"]] = step
    positions = {step["id"]: step["sequence"] for step in steps}
    for step in steps:
        for dependency in step["depends_on"]:
            if dependency not in positions:
                fail(f"step {step['id']!r} has unknown dependency {dependency!r}")
            if positions[dependency] >= step["sequence"]:
                fail(f"step {step['id']!r} depends on a non-earlier step")

    def is_ancestor(ancestor_id: str, descendant_id: str) -> bool:
        pending = list(by_id[descendant_id]["depends_on"])
        seen: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate == ancestor_id:
                return True
            if candidate not in seen:
                seen.add(candidate)
                pending.extend(by_id[candidate]["depends_on"])
        return False

    transition_steps: dict[str, dict[str, Any]] = {}
    for step in steps:
        if step["source_id"] and step["from_version"] and step["to_version"]:
            key = transition_key(step["source_id"], step["from_version"], step["to_version"])
            if key in transition_steps:
                fail(f"duplicate version transition {key}")
            transition_steps[key] = step

    expected_keys: set[str] = set()
    terminal_transitions: list[dict[str, Any]] = []
    for source_id, product_rule in snapshot["product_rules"].items():
        previous_sequence = 0
        previous_step: dict[str, Any] | None = None
        for transition in product_rule["transitions"]:
            key = transition_key(source_id, transition["from"], transition["to"])
            expected_keys.add(key)
            if key not in transition_steps:
                fail(f"missing required transition {key}")
            step = transition_steps[key]
            require_equal(step["action"], transition["action"], f"{key} action")
            if step["sequence"] <= previous_sequence:
                fail(f"transitions for {source_id} are not ordered")
            if previous_step is not None and not is_ancestor(previous_step["id"], step["id"]):
                fail(f"transition {key} does not depend on the preceding {source_id} transition")
            previous_sequence = step["sequence"]
            previous_step = step
        if previous_step is not None:
            terminal_transitions.append(previous_step)
    require_equal(set(transition_steps), expected_keys, "version transition set")

    fleet_steps = [step for step in steps if step["action"] == "deploy_fleet"]
    require_equal(len(fleet_steps), 1, "fleet-management deployment step count")
    fleet_sequence = fleet_steps[0]["sequence"]
    for key in snapshot["ordering_rules"]["fleet_management_before"]:
        if transition_steps[key]["sequence"] <= fleet_sequence:
            fail(f"Fleet Management must precede {key}")
        if not is_ancestor(fleet_steps[0]["id"], transition_steps[key]["id"]):
            fail(f"{key} must depend on Fleet Management deployment")

    content_steps: dict[str, dict[str, Any]] = {}
    for step in steps:
        for item_id in step["content_items"]:
            if item_id not in content_steps or step["sequence"] < content_steps[item_id]["sequence"]:
                content_steps[item_id] = step
    require_equal(set(content_steps), set(snapshot["content_rules"]), "migration-step content coverage")
    for key, item_ids in snapshot["ordering_rules"]["remove_before"].items():
        transition_step = transition_steps[key]
        for item_id in item_ids:
            if content_steps[item_id]["sequence"] >= transition_step["sequence"]:
                fail(f"{item_id} must be remediated before {key}")
            if not is_ancestor(content_steps[item_id]["id"], transition_step["id"]):
                fail(f"{key} must depend on the remediation of {item_id}")

    validation = [step for step in steps if step["action"] == "validate"]
    retirement = [step for step in steps if step["action"] == "retire"]
    require_equal(len(validation), 1, "validation step count")
    require_equal(len(retirement), 1, "retirement step count")
    if validation[0]["sequence"] >= retirement[0]["sequence"]:
        fail("target validation must precede legacy retirement")
    if validation[0]["id"] not in retirement[0]["depends_on"]:
        fail("legacy retirement must directly depend on target validation")
    for transition in terminal_transitions:
        if not is_ancestor(transition["id"], validation[0]["id"]):
            fail(f"target validation does not depend on terminal transition {transition['id']!r}")


def run_cli(inventory: Path, compatibility: Path, research: Path, output: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "vcf_architect",
        "--inventory",
        str(inventory),
        "--compatibility",
        str(compatibility),
        "--research",
        str(research),
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=15, check=False)
    if completed.returncode != 0:
        fail(
            "migration architect CLI failed: "
            f"stdout={completed.stdout.strip()!r}, stderr={completed.stderr.strip()!r}"
        )


def verify_cli(plan: dict[str, Any]) -> None:
    research = load_json(RESEARCH_PATH)
    require_equal(plan["research"], research, "checked-in research input")
    for index, record in enumerate(research):
        hostname = urlsplit(record["url"]).hostname
        if hostname != "broadcom.com" and (hostname is None or not hostname.endswith(".broadcom.com")):
            fail(f"research record {index} is not a Broadcom-published HTTPS source")
        try:
            date.fromisoformat(record["accessed_on"])
        except ValueError:
            fail(f"research record {index} has an invalid access date")

    with tempfile.TemporaryDirectory(prefix="vcf-architect-verification-") as temporary:
        temp = Path(temporary)
        first_output = temp / "first" / "plan.json"
        second_output = temp / "second" / "plan.json"
        run_cli(INVENTORY_PATH, SNAPSHOT_PATH, RESEARCH_PATH, first_output)
        run_cli(INVENTORY_PATH, SNAPSHOT_PATH, RESEARCH_PATH, second_output)
        require_equal(first_output.read_bytes(), second_output.read_bytes(), "deterministic CLI output")
        require_equal(first_output.read_bytes(), PLAN_PATH.read_bytes(), "checked-in generated plan")

        inventory = load_json(INVENTORY_PATH)
        snapshot = load_json(SNAPSHOT_PATH)
        modified_inventory = dict(inventory)
        modified_inventory["estate_id"] = "cli-input-estate"
        modified_snapshot = dict(snapshot)
        modified_snapshot["snapshot_id"] = "cli-input-snapshot"
        modified_research = [dict(record) for record in research]
        modified_research[0]["claims"] = ["CLI research input propagation check."]

        inventory_path = temp / "inventory.json"
        snapshot_path = temp / "compatibility.json"
        research_path = temp / "research.json"
        inventory_path.write_text(json.dumps(modified_inventory), encoding="utf-8")
        snapshot_path.write_text(json.dumps(modified_snapshot), encoding="utf-8")
        research_path.write_text(json.dumps(modified_research), encoding="utf-8")
        modified_output = temp / "modified" / "plan.json"
        run_cli(inventory_path, snapshot_path, research_path, modified_output)
        modified_plan = json.loads(modified_output.read_text(encoding="utf-8"))
        require_equal(modified_plan["estate_id"], "cli-input-estate", "CLI inventory input")
        require_equal(modified_plan["snapshot_id"], "cli-input-snapshot", "CLI compatibility input")
        require_equal(modified_plan["research"], modified_research, "CLI research input")


def main() -> int:
    # Contract requirement: validate the deliverable against the installer
    # specification before loading fixtures or performing semantic checks.
    plan = load_json(PLAN_PATH)
    schema = load_json(SCHEMA_PATH)
    validate_schema(plan, schema, schema)

    inventory = load_json(INVENTORY_PATH)
    snapshot = load_json(SNAPSHOT_PATH)
    require_equal(plan["schema_version"], 1, "plan schema version")
    require_equal(plan["estate_id"], inventory["estate_id"], "estate id")
    require_equal(plan["snapshot_id"], snapshot["snapshot_id"], "snapshot id")
    require_equal(plan["target_release"], inventory["target"]["vcf_release"], "target release")
    verify_sources(plan, inventory, snapshot)
    verify_content(plan, inventory, snapshot)
    verify_placements(plan, inventory, snapshot)
    verify_steps(plan, snapshot)
    verify_cli(plan)
    print("PASS: VCF migration architecture matches schema, estate, and pinned snapshot")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)

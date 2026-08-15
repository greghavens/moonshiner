#!/usr/bin/env python3
import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class VerificationError(AssertionError):
    pass


def fail(message):
    raise VerificationError(message)


def json_type_matches(value, expected):
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
    return True


def schema_validator(document):
    def resolve(ref):
        if not ref.startswith("#/"):
            fail(f"unsupported non-local schema reference: {ref}")
        node = document
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[token]
        return node

    def validate(value, schema, path="$", seen=None):
        if not isinstance(schema, dict):
            return
        if seen is None:
            seen = set()
        if "$ref" in schema:
            ref = schema["$ref"]
            marker = (id(value), ref)
            if marker in seen:
                return
            validate(value, resolve(ref), path, seen | {marker})
        for part in schema.get("allOf", []):
            validate(value, part, path, seen)
        if "const" in schema and value != schema["const"]:
            fail(f"{path}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            fail(f"{path}: value {value!r} is not in {schema['enum']!r}")
        expected = schema.get("type")
        if isinstance(expected, list):
            matches = any(json_type_matches(value, item) for item in expected)
        else:
            matches = expected is None or json_type_matches(value, expected)
        if not matches:
            fail(f"{path}: expected JSON type {expected}, got {type(value).__name__}")
        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    fail(f"{path}: missing required property {key!r}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extras = set(value) - set(properties)
                if extras:
                    fail(f"{path}: additional properties are forbidden: {sorted(extras)}")
            for key, child_schema in properties.items():
                if key in value:
                    validate(value[key], child_schema, f"{path}.{key}", seen)
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                fail(f"{path}: too few items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                fail(f"{path}: too many items")
            if schema.get("uniqueItems"):
                rendered = [json.dumps(item, sort_keys=True) for item in value]
                if len(rendered) != len(set(rendered)):
                    fail(f"{path}: items must be unique")
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    validate(item, item_schema, f"{path}[{index}]", seen)
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                fail(f"{path}: string is too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                fail(f"{path}: string is too long")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                fail(f"{path}: string does not match {schema['pattern']!r}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                fail(f"{path}: number is below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                fail(f"{path}: number is above maximum")

    return validate


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_client(inventory_path=None, compatibility_path=None):
    if inventory_path is None:
        inventory_path = ROOT / "estate-inventory.json"
    if compatibility_path is None:
        compatibility_path = ROOT / "compatibility-snapshot.json"
    with tempfile.TemporaryDirectory(prefix="vcfarch-0096-") as build_dir:
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                build_dir,
                str(ROOT / "MigrationArchitecture.java"),
                str(ROOT / "TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(f"javac failed:\n{compile_result.stdout}{compile_result.stderr}")
        run_result = subprocess.run(
            [
                "java",
                "-cp",
                build_dir,
                "TestMain",
                str(inventory_path),
                str(compatibility_path),
            ],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if run_result.returncode != 0:
            fail(f"TestMain failed:\n{run_result.stdout}{run_result.stderr}")
        try:
            return json.loads(run_result.stdout)
        except json.JSONDecodeError as exc:
            fail(f"client output is not one JSON artifact: {exc}")


def verify():
    artifact = run_client()

    # This is deliberately the first artifact check.  It uses the vendor's
    # unmodified SddcSpec schema before fixtures or the compatibility oracle.
    openapi = load_json(
        ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
    )
    validate_openapi = schema_validator(openapi)
    validate_openapi(artifact, openapi["components"]["schemas"]["SddcSpec"])

    plan_schema = load_json(ROOT / "migration-plan.schema.json")
    if "migrationPlan" not in artifact:
        fail("$: missing migrationPlan extension")
    schema_validator(plan_schema)(artifact["migrationPlan"], plan_schema)

    research = artifact.get("researchConsulted")
    if not isinstance(research, list) or not research:
        fail("$.researchConsulted: expected a nonempty array of consulted sources")
    seen_research_urls = set()
    for index, entry in enumerate(research):
        path = f"$.researchConsulted[{index}]"
        if not isinstance(entry, dict):
            fail(f"{path}: expected an object")
        for field in ("title", "url", "finding"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                fail(f"{path}.{field}: expected a nonblank string")
        url = entry["url"]
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            fail(f"{path}.url: expected an absolute HTTP(S) source URL")
        if hostname == "localhost" or hostname.endswith(".invalid"):
            fail(f"{path}.url: placeholder and local URLs are forbidden")
        if url in seen_research_urls:
            fail(f"{path}.url: consulted source URLs must be unique")
        seen_research_urls.add(url)

    inventory = load_json(ROOT / "estate-inventory.json")
    snapshot = load_json(ROOT / "compatibility-snapshot.json")
    plan = artifact["migrationPlan"]

    desired = inventory["desiredTargetSddcSpec"]
    for key, expected in desired.items():
        if artifact.get(key) != expected:
            fail(f"$.{key}: target SddcSpec value differs from estate fixture")

    expected_metadata = {
        "inventoryId": inventory["estateId"],
        "sourceVcfVersion": inventory["vcfVersion"],
        "targetVcfVersion": inventory["targetVcfVersion"],
        "strategy": snapshot["requiredStrategy"],
    }
    for key, expected in expected_metadata.items():
        if plan.get(key) != expected:
            fail(f"$.migrationPlan.{key}: expected {expected!r}")

    rule = snapshot["blockingRule"]
    if plan["blocker"] != rule:
        fail("$.migrationPlan.blocker does not match the pinned blocking rule")
    if rule["currentBuild"] <= rule["targetBuild"]:
        fail("invalid grading snapshot: blocker is not a back-in-time build")

    source_by_id = {item["id"]: item for item in inventory["components"]}
    target_by_id = {item["id"]: item for item in snapshot["targetComponents"]}
    transitions = plan["componentTransitions"]
    transition_ids = [item["componentId"] for item in transitions]
    if len(transition_ids) != len(set(transition_ids)):
        fail("componentTransitions contains duplicate components")
    if set(transition_ids) != set(source_by_id) or set(source_by_id) != set(target_by_id):
        fail("componentTransitions must cover every and only inventoried component")
    for item in transitions:
        component_id = item["componentId"]
        source = source_by_id[component_id]
        target = target_by_id[component_id]
        expected = {
            "componentId": component_id,
            "componentName": source["name"],
            "currentVersion": source["version"],
            "currentBuild": source["build"],
            "targetVersion": target["version"],
            "targetBuild": target["build"],
            "method": target["method"],
            "gates": target["requiredGates"],
        }
        if item != expected:
            fail(f"transition for {component_id} differs from inventory/snapshot authority")

    required_stages = snapshot["requiredStages"]
    steps = plan["steps"]
    if len(steps) != len(required_stages):
        fail("migration step count differs from pinned required sequence")
    for index, (step, expected) in enumerate(zip(steps, required_stages), start=1):
        if step["order"] != index:
            fail("migration step order values must be consecutive and one-based")
        for actual_key, expected_key in (
            ("stage", "stage"),
            ("componentIds", "componentIds"),
            ("gates", "requiredGates"),
        ):
            if step[actual_key] != expected[expected_key]:
                fail(f"migration step {index} {actual_key} differs from pinned sequence")

    forbidden_methods = {"UPGRADE_IN_PLACE", "DOWNGRADE"}
    if any(item["method"] in forbidden_methods for item in transitions):
        fail("the architecture contains an unsupported in-place or downgrade method")

    # Exercise the public API with runtime-only variants so a fixed, canned
    # artifact cannot satisfy the requirement to read both supplied inputs.
    variant_inventory = json.loads(json.dumps(inventory))
    variant_snapshot = json.loads(json.dumps(snapshot))
    variant_inventory["estateId"] = "runtime-probe-estate"
    variant_inventory["desiredTargetSddcSpec"]["sddcId"] = "probe-m01"
    variant_inventory["components"][0]["name"] = "Runtime Probe SDDC Manager"
    variant_inventory["components"][0]["build"] += 17
    variant_inventory["components"][-1]["build"] += 23
    variant_snapshot["targetComponents"][-1]["build"] += 11
    variant_snapshot["targetComponents"][-1]["requiredGates"].append(
        "RUNTIME_COMPATIBILITY_PROBE"
    )
    variant_snapshot["blockingRule"]["currentBuild"] += 23
    variant_snapshot["blockingRule"]["targetBuild"] += 11
    variant_snapshot["blockingRule"]["decision"] = (
        "NO_SUPPORTED_IN_PLACE_PATH_RUNTIME_PROBE"
    )
    variant_snapshot["requiredStages"][0]["requiredGates"].append(
        "RUNTIME_STAGE_PROBE"
    )

    with tempfile.TemporaryDirectory(prefix="vcfarch-0096-inputs-") as input_dir:
        input_root = Path(input_dir)
        inventory_path = input_root / "estate.json"
        snapshot_path = input_root / "compatibility.json"
        inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        snapshot_path.write_text(json.dumps(variant_snapshot), encoding="utf-8")
        variant_artifact = run_client(inventory_path, snapshot_path)

    variant_plan = variant_artifact.get("migrationPlan")
    if not isinstance(variant_plan, dict):
        fail("runtime variant: missing migrationPlan")
    if variant_artifact.get("sddcId") != "probe-m01":
        fail("runtime variant: desired target SddcSpec input was not honored")
    if variant_plan.get("inventoryId") != "runtime-probe-estate":
        fail("runtime variant: inventory metadata input was not honored")
    variant_transitions = {
        item.get("componentId"): item
        for item in variant_plan.get("componentTransitions", [])
        if isinstance(item, dict)
    }
    source_probe = variant_transitions.get("SDDC_MANAGER_VCF", {})
    if source_probe.get("componentName") != "Runtime Probe SDDC Manager":
        fail("runtime variant: component inventory input was not honored")
    if source_probe.get("currentBuild") != variant_inventory["components"][0]["build"]:
        fail("runtime variant: component build input was not honored")
    target_probe = variant_transitions.get("NSX_T_MANAGER", {})
    expected_target = variant_snapshot["targetComponents"][-1]
    if target_probe.get("targetBuild") != expected_target["build"]:
        fail("runtime variant: compatibility target input was not honored")
    if target_probe.get("gates") != expected_target["requiredGates"]:
        fail("runtime variant: compatibility gate input was not honored")
    if variant_plan.get("blocker") != variant_snapshot["blockingRule"]:
        fail("runtime variant: compatibility blocker input was not honored")
    variant_steps = variant_plan.get("steps", [])
    if not variant_steps or variant_steps[0].get("gates") != (
        variant_snapshot["requiredStages"][0]["requiredGates"]
    ):
        fail("runtime variant: required stage input was not honored")

    print("PASS: SddcSpec schema and pinned brownfield migration architecture validated")


if __name__ == "__main__":
    try:
        verify()
    except (VerificationError, KeyError, TypeError) as exc:
        raise SystemExit(f"FAIL: {exc}")
